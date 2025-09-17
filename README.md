# Django ProfileBoard

A production-ready Django performance profiler with live dashboard capabilities.

## Features

- **Live Dashboard**: Real-time performance monitoring with WebSocket updates
- **Production Safe**: Works with DEBUG=False and includes security controls
- **Comprehensive Profiling**: Request timing, database queries, memory usage, and external API calls
- **Query Analysis**: N+1 detection, duplicate query identification, and performance insights
- **Export Capabilities**: CSV and JSON export for CI/CD integration
- **Memory Profiling**: Optional memray integration for deep memory analysis

## Installation

Follow these steps to install and configure Django ProfileBoard.

**1. Install the package**

Install the package from PyPI:

```bash
pip install django-profileboard
```

**2. Add to INSTALLED_APPS**

Add `'django_profileboard'` to your `INSTALLED_APPS` in `settings.py`:

```python
INSTALLED_APPS = [
    # ... other apps
    'django_profileboard',
]
```

**3. Add Middleware**

Add the `RequestProfilerMiddleware` to your `MIDDLEWARE` setting in `settings.py`. It should be placed at the top of the list.

```python
MIDDLEWARE = [
    'django_profileboard.middleware.RequestProfilerMiddleware',
    # ... your other middleware
]
```

**4. Enable the Profiler**

Enable the profiler by setting `PROFILEBOARD_ENABLED` in `settings.py`. It's recommended to only enable it when needed, for example, by using an environment variable:

```python
import os
PROFILEBOARD_ENABLED = os.environ.get('PROFILEBOARD_ENABLED', 'False') == 'True'
```

**5. Configure ASGI for Live Dashboard**

For live dashboard functionality, you need to configure your `asgi.py` to handle WebSocket connections. Wrap your existing application with the `ProfileboardMiddleware`:

```python
# asgi.py
import os
from django.core.asgi import get_asgi_application
from django_profileboard.middleware import ProfileboardMiddleware

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'your_project.settings')

application = get_asgi_application()
application = ProfileboardMiddleware(application)
```

**6. Include ProfileBoard URLs**

Include the ProfileBoard URLs in your project's `urls.py`:

```python
from django.urls import path, include

urlpatterns = [
    # ... your other urls
    path('__monitor__/', include('django_profileboard.urls')),
]
```

**7. Run Migrations**

Run migrations to create the necessary database tables:

```bash
python manage.py migrate
```

## Usage

Once installed and configured, you can access the dashboard at the URL you configured (e.g., `/__monitor__/`).

### Security

The dashboard provides detailed information about your application's performance. It is **strongly recommended** to restrict access to the dashboard in a production environment. You can do this by using a custom middleware to check for superuser status or a specific permission.

Here is an example of a simple middleware to restrict access to superusers:

```python
# your_app/middleware.py
from django.http import HttpResponseForbidden

class SuperuserRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith('/__monitor__/') and not request.user.is_superuser:
            return HttpResponseForbidden()
        return self.get_response(request)
```

Then, add this middleware to your `MIDDLEWARE` setting in `settings.py`:

```python
MIDDLEWARE = [
    # ... other middleware
    'your_app.middleware.SuperuserRequiredMiddleware',
    # ... other middleware
]
```
