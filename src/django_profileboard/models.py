from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User
import json
import uuid


class SafeJSONEncoder(json.JSONEncoder):
    """JSON encoder that handles problematic data types safely"""
    def default(self, obj):
        if isinstance(obj, (bytes, bytearray)):
            return obj.decode('utf-8', errors='replace')
        if hasattr(obj, '__str__'):
            return str(obj)[:200]  # Limit string length
        return super().default(obj)


class RequestProfile(models.Model):
    """Core model for storing request performance data"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)

    # Request metadata
    url = models.URLField(max_length=2000)
    view_name = models.CharField(max_length=200, blank=True)
    method = models.CharField(max_length=10)
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)

    # Performance metrics
    duration = models.FloatField(help_text="Total request duration in seconds")
    memory_usage = models.FloatField(null=True, blank=True, help_text="Peak memory usage in MB")
    cpu_usage = models.FloatField(null=True, blank=True, help_text="CPU usage percentage")

    # Database metrics
    db_queries_count = models.IntegerField(default=0)
    db_queries_time = models.FloatField(default=0.0, help_text="Total DB query time in seconds")

    # Status and metadata
    status_code = models.IntegerField()
    is_error = models.BooleanField(default=False)

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['timestamp', 'view_name']),
            models.Index(fields=['duration']),
            models.Index(fields=['db_queries_count']),
        ]

    def __str__(self):
        return f"{self.method} {self.url} - {self.duration:.3f}s"


class DatabaseQuery(models.Model):
    """Individual database query within a request"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_profile = models.ForeignKey(RequestProfile, related_name='database_queries', on_delete=models.CASCADE)

    sql = models.TextField()
    params = models.JSONField(default=dict, encoder=SafeJSONEncoder)  # Use safe JSON encoder
    duration = models.FloatField(help_text="Query duration in seconds")
    stack_trace = models.TextField(blank=True, max_length=5000)  # Limit length

    # Query metadata
    is_duplicate = models.BooleanField(default=False)
    is_similar = models.BooleanField(default=False)  # Similar structure, different params
    similar_query_hash = models.CharField(max_length=64, blank=True, db_index=True)

    timestamp = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['timestamp']

    def __str__(self):
        return f"Query ({self.duration:.3f}s): {self.sql[:50]}..."


class MemoryProfile(models.Model):
    """Memory profiling data from memray"""
    request_profile = models.OneToOneField(RequestProfile, related_name='memory_profile', on_delete=models.CASCADE)

    peak_memory_mb = models.FloatField()
    memory_report_path = models.CharField(max_length=500, blank=True)

    # Detailed memory breakdown
    heap_size = models.FloatField(null=True, blank=True)
    allocated_objects = models.IntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)


class ExternalAPICall(models.Model):
    """External API calls tracked via OpenTelemetry"""
    request_profile = models.ForeignKey(RequestProfile, related_name='api_calls', on_delete=models.CASCADE)

    url = models.URLField()
    method = models.CharField(max_length=10)
    duration = models.FloatField()
    status_code = models.IntegerField()

    # OpenTelemetry trace data
    trace_id = models.CharField(max_length=32, blank=True)
    span_id = models.CharField(max_length=16, blank=True)

    timestamp = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['timestamp']