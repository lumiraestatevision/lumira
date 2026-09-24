from lumira_shared import BaseServiceSettings


class ParserSettings(BaseServiceSettings):
    service_name: str = "parser"
    port: int = 8001

    # PDF-Grundrisse enthalten keine Maßstabsinformation. STUB: fester Maßstab 1:N,
    # bis die Maßstabserkennung (Maßketten/Maßstabsleiste) implementiert ist.
    pdf_assumed_scale: float = 100.0
    pdf_render_dpi: int = 150
