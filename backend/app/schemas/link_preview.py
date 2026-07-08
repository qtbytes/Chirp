from pydantic import BaseModel


class LinkPreviewOut(BaseModel):
    """
    A generic link "unfurl" card built from a page's Open Graph / Twitter Card
    metadata. One shape fits every site — GitHub, YouTube, Steam, etc. — because
    they all expose the same standard ``<meta>`` tags.
    """

    url: str
    title: str
    description: str | None = None
    image: str | None = None
    site_name: str | None = None
