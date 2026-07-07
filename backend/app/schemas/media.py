import re

from pydantic import BaseModel

# Media URLs are only ever produced by the upload endpoint below, which names
# files "<uuid-hex>.<ext>". Validating incoming media_url values against this
# exact shape keeps clients from pointing posts at arbitrary paths/hosts.
MEDIA_URL_PATTERN = re.compile(r"/uploads/media/[0-9a-f]{32}\.(jpg|png|webp|gif)")


class MediaUploadOut(BaseModel):
    url: str
