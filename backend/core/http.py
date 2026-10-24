import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt


def json_body(request):
    if not request.body:
        return {}
    try:
        data = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Request body is not valid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("Request JSON must be an object")
    return data


def ok(payload, status=200):
    return JsonResponse(payload, status=status, safe=False)


def error(message, status=400):
    return JsonResponse({"error": message}, status=status)


def api_view(methods):
    methods = {item.upper() for item in methods}

    def decorator(fn):
        @csrf_exempt
        def wrapped(request, *args, **kwargs):
            if request.method == "OPTIONS":
                return ok({})
            if request.method not in methods:
                return error("Method not allowed", 405)
            try:
                return fn(request, *args, **kwargs)
            except ValueError as exc:
                return error(str(exc), 400)
            except LookupError as exc:
                return error(str(exc), 404)

        return wrapped

    return decorator
