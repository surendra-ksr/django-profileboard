from django.urls import path, include
from . import views

app_name = 'profileboard'

urlpatterns = [
    path('', views.ProfileDashboardView.as_view(), name='dashboard'),
    path('export/', views.export_profile_data, name='export_data'),
    path('query-analysis/<uuid:profile_id>/', views.query_analysis, name='query_analysis'),
]