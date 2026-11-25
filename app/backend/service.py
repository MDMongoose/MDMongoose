"""MdmService: the service core plus the service class of every feature."""

from .core.service import PAGE, FieldError, Forbidden, NotFound, ServiceCore, tile_cat  # noqa: F401
from .features import service_classes

MdmService = type("MdmService", (*service_classes(), ServiceCore), {})
