"""Support for in-place page navigation (Turbo).

Turbo submits forms in the background and only shows the answer when it is either a redirect (saved, go on)
or an error status (show the form again with its messages). Django shows a form that failed validation with
status 200, which Turbo would refuse to display. This middleware marks that answer as 422 for Turbo's own
form requests only, so the screen the user sees is exactly what a normal page load would have shown.
Nothing is changed for ordinary browser posts, JSON calls (point of sale) or the test client."""

TURBO_FORM = 'text/vnd.turbo-stream.html'


class TurboFormMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (request.method in ('POST', 'PUT', 'PATCH', 'DELETE')
                and TURBO_FORM in request.headers.get('Accept', '')
                and response.status_code == 200
                and not response.streaming
                and response.get('Content-Type', '').startswith('text/html')):
            response.status_code = 422
        return response
