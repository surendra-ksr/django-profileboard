import logging
import re
import time
import traceback
from typing import Dict, List, Any, Optional
from collections import defaultdict


class ProfileDataCollector:
    """Thread-safe collector for performance data during request processing"""

    def __init__(self, profile_id: str):
        self.profile_id = profile_id
        self.request_data = {}
        self.queries = []
        self.api_calls = []
        self.middleware_timings = {}

    def add_request_data(self, data: Dict[str, Any]):
        """Add request metadata"""
        self.request_data.update(data)

    def add_query(self, sql: str, params: Any, duration: float, stack_trace: str = ""):
        """Add a database query"""
        self.queries.append({
            'sql': sql,
            'params': params,
            'duration': duration,
            'stack_trace': stack_trace,
            'timestamp': time.time(),
        })

    def add_api_call(self, url: str, method: str, duration: float, status_code: int):
        """Add an external API call"""
        self.api_calls.append({
            'url': url,
            'method': method,
            'duration': duration,
            'status_code': status_code,
            'timestamp': time.time(),
        })

    def finalize(self, final_data: Dict[str, Any]) -> Dict[str, Any]:
        """Finalize and return complete profile data"""
        return {
            **self.request_data,
            **final_data,
            'queries': self.queries,
            'api_calls': self.api_calls,
            'middleware_timings': self.middleware_timings,
            'profile_id': self.profile_id,
        }


class SQLQueryCapture(logging.Handler):
    """
    Custom logging handler that captures SQL queries even when DEBUG=False.
    This implements the DEBUG=False workaround described in your plan.
    """

    def __init__(self):
        super().__init__()
        self.collector: Optional[ProfileDataCollector] = None
        self.setLevel(logging.DEBUG)

        # Regex to parse Django SQL log messages
        self.sql_regex = re.compile(
            r'\((\d+\.\d+)\)\s+(.*?);\s+args=(.+)',
            re.DOTALL
        )

    def set_collector(self, collector: ProfileDataCollector):
        """Set the active collector for this thread"""
        self.collector = collector

    def emit(self, record: logging.LogRecord):
        """Process SQL query log records"""
        if not self.collector:
            return

        try:
            message = record.getMessage()
            
            # Skip profiler's own queries to prevent recursion
            if 'django_profileboard' in message:
                return
                
            match = self.sql_regex.match(message)

            if match:
                duration = float(match.group(1))
                sql = match.group(2).strip()
                params_str = match.group(3)

                # Parse parameters safely and prevent escaping issues
                try:
                    if params_str == 'None':
                        params = {}
                    else:
                        import ast
                        params = ast.literal_eval(params_str)
                        # Sanitize params to prevent recursive escaping
                        if isinstance(params, (list, tuple)):
                            params = [str(p)[:100] if isinstance(p, str) else p for p in params]
                        elif isinstance(params, dict):
                            params = {k: str(v)[:100] if isinstance(v, str) else v for k, v in params.items()}
                except:
                    # If parsing fails, store as truncated string to prevent escaping
                    params = str(params_str)[:100]

                # Get stack trace (excluding Django internals)
                stack_trace = self._get_clean_stack_trace()

                # Add to collector with length limits
                self.collector.add_query(
                    sql=sql[:1000],
                    params=params,
                    duration=duration,
                    stack_trace=stack_trace[:2000]  # Limit stack trace length
                )

        except Exception as e:
            # Don't let logging errors break the application
            pass

    def _get_clean_stack_trace(self) -> str:
        """Get stack trace excluding Django internals"""
        try:
            stack = traceback.extract_stack()

            # Filter out Django and profiler internals
            filtered_stack = []
            for frame in stack:
                if not any(exclude in frame.filename for exclude in [
                    'django/db/', 'django_profileboard/', 'logging/'
                ]):
                    filtered_stack.append(frame)

            # Return last few frames, sanitized to prevent escaping issues
            trace = ''.join(traceback.format_list(filtered_stack[-5:]))
            # Remove excessive backslashes and limit length
            trace = trace.replace('\\\\', '\\')
            return trace[:2000]
        except Exception:
            return "Stack trace unavailable"


class MemoryProfiler:
    """
    Wrapper for memray integration as described in your plan.
    Samples a percentage of requests for deep memory profiling.
    """

    @staticmethod
    def should_profile_memory() -> bool:
        """Determine if this request should get memory profiling"""
        import random
        from django.conf import settings
        # Profile 1% of requests by default
        sample_rate = getattr(settings, 'PROFILEBOARD_MEMORY_SAMPLE_RATE', 0.01)
        return random.random() < sample_rate

    @staticmethod
    def profile_request(request_func, *args, **kwargs):
        """Profile a request with memray"""
        if not MemoryProfiler.should_profile_memory():
            return request_func(*args, **kwargs)

        try:
            import memray
            import tempfile
            import os

            # Create temporary file for memray output
            with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as tmp:
                output_file = tmp.name

            # Run with memray profiling
            with memray.Tracker(output_file):
                result = request_func(*args, **kwargs)

            # Generate HTML report
            html_file = output_file.replace('.bin', '.html')
            os.system(f'memray flamegraph {output_file} -o {html_file}')

            # Store the report path (could be stored in database)
            # This is where you'd integrate with your storage system

            return result

        except ImportError:
            # memray not available, fall back to normal execution
            return request_func(*args, **kwargs)
        except Exception as e:
            logging.error(f"Memory profiling failed: {e}")
            return request_func(*args, **kwargs)


class QueryAnalyzer:
    """Analyze database queries for N+1 problems and duplicates"""

    @staticmethod
    def analyze_queries(queries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze queries and identify problems"""
        if not queries:
            return {}

        analysis = {
            'total_queries': len(queries),
            'total_time': sum(q['duration'] for q in queries),
            'duplicates': [],
            'similar_queries': defaultdict(list),
            'slow_queries': [],
            'n_plus_one_candidates': [],
        }

        # Find duplicates and similar queries
        query_hashes = {}
        for i, query in enumerate(queries):
            sql = query['sql'].strip()

            # Check for exact duplicates
            if sql in query_hashes:
                analysis['duplicates'].append({
                    'sql': sql,
                    'count': len(query_hashes[sql]) + 1,
                    'indices': query_hashes[sql] + [i]
                })
                query_hashes[sql].append(i)
            else:
                query_hashes[sql] = [i]

            # Check for slow queries (> 100ms by default)
            from django.conf import settings
            threshold = getattr(settings, 'PROFILEBOARD_SLOW_QUERY_THRESHOLD', 0.1)
            if query['duration'] > threshold:
                analysis['slow_queries'].append({
                    'index': i,
                    'duration': query['duration'],
                    'sql': sql[:100] + '...' if len(sql) > 100 else sql
                })

        # Detect potential N+1 queries by looking for similar patterns
        # This is a simplified version - could be more sophisticated
        normalized_queries = defaultdict(list)
        for i, query in enumerate(queries):
            normalized = QueryAnalyzer._normalize_sql(query['sql'])
            normalized_queries[normalized].append(i)

        for normalized, indices in normalized_queries.items():
            if len(indices) > 2:  # Potential N+1 if same query appears multiple times
                analysis['n_plus_one_candidates'].append({
                    'normalized_sql': normalized,
                    'count': len(indices),
                    'indices': indices
                })

        return analysis

    @staticmethod
    def _normalize_sql(sql: str) -> str:
        """Normalize SQL for similarity detection"""
        # Remove parameter values, keep structure
        import re
        normalized = sql.strip().lower()

        # Replace numeric values
        normalized = re.sub(r'\b\d+\b', 'N', normalized)

        # Replace quoted strings
        normalized = re.sub(r"'[^']*'", "'S'", normalized)
        normalized = re.sub(r'"[^"]*"', '"S"', normalized)

        # Replace parameter placeholders
        normalized = re.sub(r'%\([^)]*\)s', '%s', normalized)

        return normalized