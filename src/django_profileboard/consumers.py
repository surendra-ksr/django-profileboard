import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser


class ProfileDashboardConsumer(AsyncWebsocketConsumer):
    """WebSocket consumer for real-time dashboard updates"""

    async def connect(self):
        """Handle WebSocket connection"""
        # Check authentication and permissions
        user = self.scope["user"]

        if isinstance(user, AnonymousUser):
            await self.close(code=4001)  # Unauthorized
            return

        if not await self._has_profiler_permission(user):
            await self.close(code=4003)  # Forbidden
            return

        # Join the profiler group for broadcasts
        self.group_name = "profiler_dashboard"
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )

        await self.accept()

        # Send initial data
        await self._send_initial_data()

    async def disconnect(self, close_code):
        """Handle WebSocket disconnection"""
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(
                self.group_name,
                self.channel_name
            )

    async def receive(self, text_data):
        """Handle incoming WebSocket messages"""
        try:
            data = json.loads(text_data)
            message_type = data.get('type')

            if message_type == 'request_history':
                await self._send_request_history(data.get('params', {}))
            elif message_type == 'request_details':
                await self._send_request_details(data.get('request_id'))
            elif message_type == 'toggle_profiler':
                await self._toggle_profiler(data.get('enabled', False))

        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': 'Invalid JSON'
            }))

    async def profile_update(self, event):
        """Handle profile updates from the signal"""
        await self.send(text_data=json.dumps({
            'type': 'profile_update',
            'data': event['profile_data']
        }))

    async def _has_profiler_permission(self, user) -> bool:
        """Check if user has permission to view profiler"""
        return await database_sync_to_async(
            lambda: user.is_staff or user.has_perm('django_profileboard.view_dashboard')
        )()

    async def _send_initial_data(self):
        """Send initial dashboard data"""
        from .models import RequestProfile

        # Get recent request data
        recent_requests = await database_sync_to_async(
            lambda: list(RequestProfile.objects.select_related('user')
            .prefetch_related('database_queries')
            .order_by('-timestamp')[:50]
            .values(
                'id', 'timestamp', 'url', 'view_name', 'method',
                'duration', 'memory_usage', 'db_queries_count',
                'db_queries_time', 'status_code', 'is_error'
            ))
        )()

        await self.send(text_data=json.dumps({
            'type': 'initial_data',
            'recent_requests': recent_requests,
            'stats': await self._get_dashboard_stats()
        }))

    async def _send_request_history(self, params):
        """Send filtered request history"""
        from .models import RequestProfile
        from django.utils import timezone
        from datetime import timedelta

        # Build query based on parameters
        queryset = RequestProfile.objects.all()

        # Time filtering
        time_range = params.get('time_range', '1h')
        if time_range == '1h':
            since = timezone.now() - timedelta(hours=1)
        elif time_range == '24h':
            since = timezone.now() - timedelta(days=1)
        elif time_range == '7d':
            since = timezone.now() - timedelta(days=7)
        else:
            since = timezone.now() - timedelta(hours=1)

        queryset = queryset.filter(timestamp__gte=since)

        # View name filtering
        if params.get('view_name'):
            queryset = queryset.filter(view_name__icontains=params['view_name'])

        # Status filtering
        if params.get('status'):
            if params['status'] == 'error':
                queryset = queryset.filter(is_error=True)
            elif params['status'] == 'slow':
                threshold = params.get('slow_threshold', 1.0)
                queryset = queryset.filter(duration__gt=threshold)

        requests = await database_sync_to_async(
            lambda: list(queryset.order_by('-timestamp')[:100].values(
                'id', 'timestamp', 'url', 'view_name', 'method',
                'duration', 'memory_usage', 'db_queries_count',
                'db_queries_time', 'status_code', 'is_error'
            ))
        )()

        await self.send(text_data=json.dumps({
            'type': 'request_history',
            'requests': requests
        }))

    async def _send_request_details(self, request_id):
        """Send detailed information about a specific request"""
        from .models import RequestProfile

        try:
            request_data = await database_sync_to_async(
                lambda: RequestProfile.objects.select_related('memory_profile')
                .prefetch_related('database_queries', 'api_calls')
                .get(id=request_id)
            )()

            # Convert to dictionary for JSON serialization
            details = {
                'id': str(request_data.id),
                'timestamp': request_data.timestamp.isoformat(),
                'url': request_data.url,
                'view_name': request_data.view_name,
                'method': request_data.method,
                'duration': request_data.duration,
                'memory_usage': request_data.memory_usage,
                'status_code': request_data.status_code,
                'is_error': request_data.is_error,
                'database_queries': [
                    {
                        'sql': query.sql,
                        'duration': query.duration,
                        'params': query.params,
                        'stack_trace': query.stack_trace,
                    }
                    for query in request_data.database_queries.all()
                ],
                'api_calls': [
                    {
                        'url': call.url,
                        'method': call.method,
                        'duration': call.duration,
                        'status_code': call.status_code,
                    }
                    for call in request_data.api_calls.all()
                ]
            }

            await self.send(text_data=json.dumps({
                'type': 'request_details',
                'details': details
            }))

        except RequestProfile.DoesNotExist:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': 'Request not found'
            }))

    async def _toggle_profiler(self, enabled):
        """Toggle profiler on/off via feature flag"""
        try:
            from flags.state import set_flag_state
            set_flag_state('PERFORMANCE_PROFILER_ENABLED', enabled)

            await self.send(text_data=json.dumps({
                'type': 'profiler_toggled',
                'enabled': enabled
            }))

        except Exception as e:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': f'Failed to toggle profiler: {str(e)}'
            }))

    async def _get_dashboard_stats(self):
        """Get aggregated dashboard statistics"""
        from .models import RequestProfile
        from django.utils import timezone
        from datetime import timedelta
        from django.db.models import Avg, Count, Sum

        since = timezone.now() - timedelta(hours=1)

        stats = await database_sync_to_async(
            lambda: RequestProfile.objects.filter(timestamp__gte=since).aggregate(
                total_requests=Count('id'),
                avg_duration=Avg('duration'),
                avg_db_queries=Avg('db_queries_count'),
                total_db_time=Sum('db_queries_time'),
                error_count=Count('id', filter=models.Q(is_error=True))
            )
        )()

        return stats or {}
