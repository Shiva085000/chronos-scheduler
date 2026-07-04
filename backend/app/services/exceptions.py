"""Service-layer errors.

Services raise these; the API layer maps them to HTTP status codes. This
keeps HTTP concerns out of business logic and lets the worker call the
same services without FastAPI in the stack.
"""


class ServiceError(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class NotFoundError(ServiceError):
    pass


class ConflictError(ServiceError):
    """State-machine violation, e.g. cancelling a job that already ran."""


class AuthenticationError(ServiceError):
    pass


class EmailAlreadyRegisteredError(ServiceError):
    pass
