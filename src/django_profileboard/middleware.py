import time
import threading
import logging
import hashlib
import traceback
import resource
from typing import Optional, Dict, Any
from uuid import uuid4

from django.conf import settings
from django.urls import resolve
from django.utils.deprecation import MiddlewareMixin
from django.dispatch import receiver
from django.core.signals import request_started, request_finished
from django.db import connection
from django.contrib.auth.models import AnonymousUser

from .models import RequestProfile, DatabaseQuery
from .utils import ProfileDataCollector, SQLQueryCapture
from .signals import profile_data_ready


class RequestProfilerMiddleware(MiddlewareMixin):
    """
    Core middleware for capturing request performance data.
    Implements the architecture described in your plan with DEBUG=False support.
    """

    def __init__(self, get_response):
        super().__init__(get_response)
        self.get_response = get_response

        # Thread-local storage for profile data
        self._local = threading.local()

        # Initialize SQL query capture with DEBUG=False workaround
        self._init_sql_capture()

    def _init_sql_capture(self):
        """Initialize SQL query capture that works with DEBUG=False"""
        # Create custom handler for django.db.backends logger
        self.sql_handler = SQLQueryCapture()

        # Get the logger and add our handler
        db_logger = logging.getLogger('django.db.backends')
        db_logger.addHandler(self.sql_handler)
        db_logger.setLevel(logging.DEBUG)

        # Force the connection to use debug cursor regardless of DEBUG setting
        if hasattr(connection, 'force_debug_cursor'):
            connection.force_debug_cursor = True

    def process_request(self, request):
        """Start profiling when request begins"""
        if not self._should_profile(request):
            return None

        # Initialize profile data collector for this request
        profile_id = str(uuid4())
        collector = ProfileDataCollector(profile_id)

        # Store in thread-local storage
        self._local.collector = collector
        self._local.start_time = time.time()
        self._local.start_memory = self._get_memory_usage()

        # Connect SQL handler to this collector
        self.sql_handler.set_collector(collector)

        # Store request metadata
        collector.add_request_data({
            'url': request.get_full_path(),
            'method': request.method,
            'user_id': request.user.id if not isinstance(request.user, AnonymousUser) else None,
            'view_name': self._get_view_name(request),
        })

        return None

    def process_response(self, request, response):
        """Complete profiling and store results"""
        if not hasattr(self._local, 'collector'):
            return response

        try:
            collector = self._local.collector

            # Calculate final metrics
            end_time = time.time()
            duration = end_time - self._local.start_time
            end_memory = self._get_memory_usage()
            memory_used = max(0, end_memory - self._local.start_memory)

            # Finalize the profile data
            profile_data = collector.finalize({
                'duration': duration,
                'memory_usage': memory_used,
                'status_code': response.status_code,
                'is_error': response.status_code >= 400,
            })

            # Store to database asynchronously
            self._store_profile_async(profile_data)

            # Broadcast to live dashboard
            profile_data_ready.send(
                sender=self.__class__,
                profile_data=profile_data,
                request_id=collector.profile_id
            )

        except Exception as e:
            # Don't let profiling errors break the actual response
            logging.error(f"ProfileBoard error: {e}", exc_info=True)
        finally:
            # Clean up thread-local data
            if hasattr(self._local, 'collector'):
                delattr(self._local, 'collector')
            if hasattr(self._local, 'start_time'):
                delattr(self._local, 'start_time')
            if hasattr(self._local, 'start_memory'):
                delattr(self._local, 'start_memory')

        return response

    def _should_profile(self, request) -> bool:
        """Determine if this request should be profiled"""
        # Check if profiler is enabled in settings
        if not getattr(settings, 'PROFILEBOARD_ENABLED', True):
            return False

        # Skip profiling for profiler dashboard itself and profiler operations
        if request.path.startswith('/__monitor__'):
            return False

        # Skip profiling database operations from the profiler itself
        if hasattr(self._local, 'profiling_in_progress'):
            return False

        # Skip static files
        if request.path.startswith('/static/') or request.path.startswith('/media/'):
            return False

        # Skip common noise requests
        if request.path.startswith('/.well-known/') or request.path.startswith('/ws/'):
            return False

        return True

    def _get_view_name(self, request) -> str:
        """Get the view name for the current request"""
        try:
            resolved = resolve(request.path_info)
            return f"{resolved.view_name or resolved.func.__name__}"
        except Exception:
            return "unknown"

    def _get_memory_usage(self) -> float:
        """Get current memory usage in MB"""
        try:
            # Get RSS (Resident Set Size) in MB
            usage = resource.getrusage(resource.RUSAGE_SELF)
            # Convert from KB to MB on Linux, already in bytes on macOS
            if hasattr(resource, 'getpagesize'):
                return (usage.ru_maxrss * resource.getpagesize()) / (1024 * 1024)
            else:
                return usage.ru_maxrss / 1024  # Already in KB on Linux
        except Exception:
            return 0.0

    def _store_profile_async(self, profile_data: Dict[str, Any]):
        """Store profile data to database (could be made async with Celery)"""
        # Prevent recursive profiling
        self._local.profiling_in_progress = True
        try:
            # Create RequestProfile instance
            request_profile = RequestProfile.objects.create(
                url=profile_data['url'],
                view_name=profile_data['view_name'],
                method=profile_data['method'],
                user_id=profile_data.get('user_id'),
                duration=profile_data['duration'],
                memory_usage=profile_data['memory_usage'],
                status_code=profile_data['status_code'],
                is_error=profile_data['is_error'],
                db_queries_count=len(profile_data.get('queries', [])),
                db_queries_time=sum(q['duration'] for q in profile_data.get('queries', [])),
            )

            # Create DatabaseQuery instances
            for query_data in profile_data.get('queries', []):
                DatabaseQuery.objects.create(
                    request_profile=request_profile,
                    sql=query_data['sql'],
                    params=query_data.get('params', {}),
                    duration=query_data['duration'],
                    stack_trace=query_data.get('stack_trace', ''),
                    similar_query_hash=self._calculate_query_hash(query_data['sql']),
                )

        except Exception as e:
            logging.error(f"Failed to store profile data: {e}", exc_info=True)
        finally:
            # Clear the flag
            if hasattr(self._local, 'profiling_in_progress'):
                delattr(self._local, 'profiling_in_progress')

    def _calculate_query_hash(self, sql: str) -> str:
        """Calculate hash for similar query detection"""
        # Normalize SQL by removing parameter placeholders
        normalized = sql.strip().lower()
        # Simple normalization - could be more sophisticated
        import re
        normalized = re.sub(r'%\([^)]*\)s', '?', normalized)
        normalized = re.sub(r'\?+', '?', normalized)
        return hashlib.md5(normalized.encode()).hexdigest()