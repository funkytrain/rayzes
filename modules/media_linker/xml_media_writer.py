"""
Aplica los links de media a un archivo .gramps (XML gzip) usando XmlEditor.
Devuelve los bytes del archivo modificado listo para descargar.
"""

from __future__ import annotations

import gzip
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from gramps_link_images import XmlEditor  # noqa: E402


def apply_media_links_xml(content_bytes: bytes, links: list[dict]) -> bytes:
    """
    Inserta objetos media y sus referencias en el XML del .gramps.

    links: lista de dicts con:
      - img_path:    ruta absoluta a la imagen
      - descripcion: texto descriptivo (stem del archivo)
      - elem_handle: handle del evento/familia/persona destino

    Devuelve los bytes gzip del archivo modificado.
    """
    # Descomprimir si es gzip
    if content_bytes[:2] == b"\x1f\x8b":
        xml_texto = gzip.decompress(content_bytes).decode("utf-8")
    else:
        xml_texto = content_bytes.decode("utf-8")

    editor = XmlEditor(xml_texto)

    for link in links:
        media_handle = editor.agregar_media_object(
            link["img_path"], link["descripcion"]
        )
        editor.agregar_objref(link["elem_handle"], media_handle)

    resultado = editor.aplicar()
    return gzip.compress(resultado.encode("utf-8"))
