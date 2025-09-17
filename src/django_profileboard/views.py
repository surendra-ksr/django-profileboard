from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView
from django.db.models import Count, Avg, Sum, Q, Max
from django.utils import timezone
from django.core.serializers.json import DjangoJSONEncoder
from django.conf import settings
from datetime import timedelta
import csv
import json

from .models import RequestProfile, DatabaseQuery
from .utils import QueryAnalyzer


def is_staff_or_profiler_admin(user):
    """Check if user can access profiler dashboard"""
    return user.is_staff or user.has_perm('django_profileboard.view_dashboard')


@method_decorator(login_required, name='dispatch')
@method_decorator(user_passes_test(is_staff_or_profiler_admin), name='dispatch')
class ProfileDashboardView(TemplateView):
    """Main dashboard view"""
    template_name = 'profileboard/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Get time range from request
        time_range = self.request.GET.get('time_range', '1h')
        
        # Add initial data for dashboard
        stats = self._get_initial_stats(time_range)
        context.update({
            'websocket_url': getattr(settings, 'PROFILEBOARD_WEBSOCKET_ENABLED', False),
            'dashboard_stats': json.dumps(stats, cls=DjangoJSONEncoder),
            'profiler_enabled': getattr(settings, 'PROFILEBOARD_ENABLED', True),
        })

        return context

    def _get_initial_stats(self, time_range='1h'):
        """Get initial statistics for dashboard"""
        # Parse time range
        if time_range == '1m':
            since = timezone.now() - timedelta(minutes=1)
        elif time_range == '5m':
            since = timezone.now() - timedelta(minutes=5)
        elif time_range == '30m':
            since = timezone.now() - timedelta(minutes=30)
        elif time_range == '1h':
            since = timezone.now() - timedelta(hours=1)
        elif time_range == '24h':
            since = timezone.now() - timedelta(days=1)
        elif time_range == '7d':
            since = timezone.now() - timedelta(days=7)
        else:
            since = timezone.now() - timedelta(hours=1)
        
        stats = RequestProfile.objects.filter(timestamp__gte=since).aggregate(
            total_requests=Count('id'),
            avg_duration=Avg('duration'),
            error_count=Count('id', filter=Q(is_error=True)),
            slowest_request=Max('duration'),
        )
        
        # Calculate error rate safely
        if stats['total_requests'] and stats['total_requests'] > 0:
            stats['error_rate'] = (stats['error_count'] * 100.0) / stats['total_requests']
        else:
            stats['error_rate'] = 0
            
        # Add recent requests for display
        recent_requests = RequestProfile.objects.filter(
            timestamp__gte=since
        ).order_by('-timestamp')[:50]
        
        stats['recent_requests'] = [
            {
                'id': str(req.id),
                'timestamp': req.timestamp.isoformat(),
                'url': req.url,
                'view_name': req.view_name,
                'method': req.method,
                'duration': req.duration,
                'status_code': req.status_code,
                'is_error': req.is_error,
                'db_queries_count': req.db_queries_count,
                'memory_usage': req.memory_usage or 0
            }
            for req in recent_requests
        ]
        
        return stats


@login_required
@user_passes_test(is_staff_or_profiler_admin)
@require_http_methods(["GET"])
def export_profile_data(request):
    """Export profile data as CSV for CI/CD integration"""

    # Get date range from request
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    format_type = request.GET.get('format', 'csv')

    # Build queryset
    queryset = RequestProfile.objects.select_related('user').prefetch_related('database_queries')

    if start_date:
        queryset = queryset.filter(timestamp__gte=start_date)
    if end_date:
        queryset = queryset.filter(timestamp__lte=end_date)

    queryset = queryset.order_by('-timestamp')

    if format_type == 'json':
        return _export_json(queryset)
    else:
        return _export_csv(queryset)


def _export_csv(queryset):
    """Export data as CSV"""
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="profile_data.csv"'

    writer = csv.writer(response)
    writer.writerow([
        'Timestamp', 'URL', 'View Name', 'Method', 'Duration (s)',
        'Memory Usage (MB)', 'DB Queries Count', 'DB Queries Time (s)',
        'Status Code', 'Is Error', 'User'
    ])

    for profile in queryset:
        writer.writerow([
            profile.timestamp.isoformat(),
            profile.url,
            profile.view_name,
            profile.method,
            profile.duration,
            profile.memory_usage or 0,
            profile.db_queries_count,
            profile.db_queries_time,
            profile.status_code,
            profile.is_error,
            profile.user.username if profile.user else 'Anonymous'
        ])

    return response


def _export_json(queryset):
    """Export data as JSON"""
    data = []

    for profile in queryset:
        data.append({
            'timestamp': profile.timestamp.isoformat(),
            'url': profile.url,
            'view_name': profile.view_name,
            'method': profile.method,
            'duration': profile.duration,
            'memory_usage': profile.memory_usage,
            'db_queries_count': profile.db_queries_count,
            'db_queries_time': profile.db_queries_time,
            'status_code': profile.status_code,
            'is_error': profile.is_error,
            'user': profile.user.username if profile.user else None,
            'queries': [
                {
                    'sql': query.sql,
                    'duration': query.duration,
                    'params': query.params,
                }
                for query in profile.database_queries.all()
            ]
        })

    response = JsonResponse({'profiles': data})
    response['Content-Disposition'] = 'attachment; filename="profile_data.json"'

    return response


@login_required
@user_passes_test(is_staff_or_profiler_admin)
@require_http_methods(["GET"])
def query_analysis(request, profile_id):
    """Get detailed query analysis for a specific request"""
    profile = get_object_or_404(RequestProfile, id=profile_id)

    queries = list(profile.database_queries.values(
        'sql', 'duration', 'params', 'stack_trace'
    ))

    analysis = QueryAnalyzer.analyze_queries(queries)

    return JsonResponse({
        'profile_id': str(profile.id),
        'analysis': analysis
    })


@login_required
@user_passes_test(is_staff_or_profiler_admin)
@require_http_methods(["GET"])
def request_details_api(request, request_id):
    """Get request details via API"""
    profile = get_object_or_404(RequestProfile, id=request_id)
    
    queries = list(profile.database_queries.values(
        'sql', 'duration', 'params'
    )[:10])  # Limit to 10 queries
    
    return JsonResponse({
        'id': str(profile.id),
        'url': profile.url,
        'method': profile.method,
        'view_name': profile.view_name,
        'duration': profile.duration,
        'status_code': profile.status_code,
        'memory_usage': profile.memory_usage or 0,
        'db_queries_count': profile.db_queries_count,
        'timestamp': profile.timestamp.isoformat(),
        'is_error': profile.is_error,
        'database_queries': queries,
        'api_calls': []  # No API calls tracked yet
    })