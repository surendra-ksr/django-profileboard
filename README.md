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

```bash
pip install django-profileboard