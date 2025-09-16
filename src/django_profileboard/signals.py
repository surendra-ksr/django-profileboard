import django.dispatch
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

# Define custom signals
profile_data_ready = django.dispatch.Signal()


@profile_data_ready.connect
def broadcast_profile_data(sender, profile_data, request_id, **kwargs):
    """Broadcast new profile data to WebSocket clients"""
    channel_layer = get_channel_layer()
    if channel_layer:
        async_to_sync(channel_layer.group_send)(
            "profiler_dashboard",
            {
                "type": "profile_update",
                "profile_data": profile_data,
                "request_id": request_id,
            }
        )
