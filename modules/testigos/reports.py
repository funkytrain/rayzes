"""
Generadores de informes HTML/PDF para el módulo de testigos.
Funciones puras: no importan Streamlit, no acceden a estado global.
"""

from translations import t


def generate_witness_html_report(
    witness_name: str,
    events_df,
    stats_dict: dict,
    folium_map_html=None,
    plotly_chart_html=None,
    title=None,
) -> str:
    """Genera un informe HTML auto-contenido para un testigo."""
    from datetime import datetime as _dt_html
    title = title or t("informe_html_titulo_testigo", name=witness_name)
    report_date = _dt_html.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    stats_rows = "\n".join(
        f"<tr><td><b>{k}</b></td><td>{v}</td></tr>"
        for k, v in stats_dict.items()
        if not k.startswith('_')
    )

    display_cols = [t("notas_col_fecha"), t("explorar_tipo_evento"), t("notas_col_lugar"), t("notas_col_sujeto"), t("notas_col_nota")]
    actual_cols = [c for c in display_cols if c in events_df.columns]
    events_rows = ""
    for _, row in events_df.iterrows():
        cells = "".join(f"<td>{str(row.get(c, ''))}</td>" for c in actual_cols)
        events_rows += f"<tr>{cells}</tr>\n"
    header_cells = "".join(
        f'<th onclick="sortTable({i})">{c}</th>' for i, c in enumerate(actual_cols)
    )

    map_section = ""
    if folium_map_html:
        map_section = (
            f"<h2>{t('sub_tray_geografica')}</h2>"
            "<div style='width:100%;height:520px;overflow:hidden;border:1px solid #ccc;'>"
            + folium_map_html
            + "</div>"
        )

    chart_section = f"<h2>{t('sub_actividad_anio')}</h2>{plotly_chart_html}" if plotly_chart_html else ""

    sort_js = """
<script>
function sortTable(n) {
  var table = document.getElementById("evtTable");
  var rows, switching, i, x, y, shouldSwitch, dir, switchcount = 0;
  switching = true; dir = "asc";
  while (switching) {
    switching = false; rows = table.rows;
    for (i = 1; i < rows.length - 1; i++) {
      shouldSwitch = false;
      x = rows[i].getElementsByTagName("TD")[n];
      y = rows[i+1].getElementsByTagName("TD")[n];
      var cmp = dir == "asc"
        ? x.innerHTML.toLowerCase() > y.innerHTML.toLowerCase()
        : x.innerHTML.toLowerCase() < y.innerHTML.toLowerCase();
      if (cmp) { shouldSwitch = true; break; }
    }
    if (shouldSwitch) {
      rows[i].parentNode.insertBefore(rows[i+1], rows[i]);
      switching = true; switchcount++;
    } else if (switchcount == 0 && dir == "asc") { dir = "desc"; switching = true; }
  }
}
</script>"""

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <title>{title}</title>
  <style>
    body {{ font-family: Georgia, serif; margin: 2em; color: #222; background: #fff; }}
    h1 {{ color: #4a0e0e; border-bottom: 2px solid #4a0e0e; padding-bottom: .3em; }}
    h2 {{ color: #333; margin-top: 2em; border-bottom: 1px solid #ddd; padding-bottom: .2em; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1em 0; font-size: .92em; }}
    th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: left; }}
    th {{ background: #f0e6d3; cursor: pointer; user-select: none; }}
    th:hover {{ background: #e0d0b0; }}
    tr:nth-child(even) {{ background: #fafafa; }}
    .stats-table td {{ width: 50%; }}
    .footer {{ color: #888; font-size: .85em; margin-top: 3em; border-top: 1px solid #ccc; padding-top: 1em; }}
  </style>
  {sort_js}
</head>
<body>
  <h1>{title}</h1>
  <p style="color:#666;">{t("informe_html_generado", date=report_date)}</p>

  <h2>{t("sub_perfil_testigo")}</h2>
  <table class="stats-table"><tbody>{stats_rows}</tbody></table>

  {map_section}

  {chart_section}

  <h2>{t("menu_explorar")} ({len(events_df)})</h2>
  <table id="evtTable">
    <thead><tr>{header_cells}</tr></thead>
    <tbody>{events_rows}</tbody>
  </table>

  <div class="footer">Genealogía Testigos &mdash; {report_date}</div>
</body>
</html>"""
    return html


def generate_family_html_report(
    family_surname: str,
    events_df,
    stats_dict: dict,
    folium_map_html=None,
    endogamy_dict=None,
    plotly_chart_html=None,
    title=None,
) -> str:
    """Genera informe HTML auto-contenido para una familia (apellido de sujeto)."""
    import datetime as _dt
    report_date = _dt.date.today().isoformat()
    title = title or t("informe_html_titulo_familia", name=family_surname)

    stats_rows = ""
    for k, v in stats_dict.items():
        stats_rows += f"<tr><td><b>{k}</b></td><td>{v}</td></tr>\n"

    events_rows = ""
    if events_df is not None and not events_df.empty:
        for _, row in events_df.iterrows():
            events_rows += (
                f"<tr>"
                f"<td>{row.get('date_iso','')}</td>"
                f"<td>{row.get('type','')}</td>"
                f"<td>{row.get('place_name','')}</td>"
                f"<td>{row.get('subj_name','')}</td>"
                f"<td>{row.get('witness_canon') or row.get('witness_raw','')}</td>"
                f"<td>{str(row.get('note',''))[:120]}</td>"
                f"</tr>\n"
            )

    endo_section = ""
    if endogamy_dict:
        endo_section = f"""
        <h2>Análisis de endogamia</h2>
        <table><thead><tr><th>Métrica</th><th>Valor</th></tr></thead><tbody>
        {"".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in endogamy_dict.items())}
        </tbody></table>
        """

    map_section = ""
    if folium_map_html:
        map_section = f"<h2>Mapa geográfico</h2><div style='width:100%;height:450px'>{folium_map_html}</div>"

    chart_section = ""
    if plotly_chart_html:
        chart_section = f"<h2>Actividad temporal</h2>{plotly_chart_html}"

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <title>{title}</title>
  <style>
    body {{font-family: Georgia, serif; max-width: 1100px; margin: 0 auto; padding: 20px; color: #222;}}
    h1 {{color: #4a3728; border-bottom: 2px solid #c8a96e; padding-bottom: 8px;}}
    h2 {{color: #6b4c2a; margin-top: 32px;}}
    table {{border-collapse: collapse; width: 100%; margin-bottom: 24px;}}
    th {{background: #f0e6d3; color: #4a3728; padding: 8px; text-align: left; border: 1px solid #d4b896;}}
    td {{padding: 6px 8px; border: 1px solid #e0cdb5; vertical-align: top;}}
    tr:nth-child(even) {{background: #faf5ef;}}
    .footer {{margin-top: 40px; color: #999; font-size: 12px; border-top: 1px solid #ddd; padding-top: 8px;}}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <h2>{t("informe_html_estadisticas")}</h2>
  <table><thead><tr><th>{t("informe_html_metrica")}</th><th>{t("informe_html_valor")}</th></tr></thead><tbody>
  {stats_rows}
  </tbody></table>
  {map_section}
  {chart_section}
  <h2>{t("informe_html_eventos")}</h2>
  <table>
    <thead><tr><th>{t("informe_html_col_fecha")}</th><th>{t("informe_html_col_tipo")}</th><th>{t("informe_html_col_lugar")}</th><th>{t("informe_html_col_sujeto")}</th><th>{t("informe_html_col_testigo")}</th><th>{t("informe_html_col_nota")}</th></tr></thead>
    <tbody>{events_rows}</tbody>
  </table>
  {endo_section}
  <div class="footer">{t("informe_html_footer", date=report_date)}</div>
</body>
</html>"""
    return html


def generate_network_html_report(_df, _by_witness: dict, _places_index: dict, top_n: int = 20) -> str:
    """Genera informe HTML auto-contenido con estadísticas generales de la red."""
    import datetime as _dt
    report_date = _dt.date.today().isoformat()

    total_events = len(_df) if _df is not None else 0
    unique_witnesses = len(_by_witness)
    unique_places = int(_df['place_name'].nunique()) if _df is not None and 'place_name' in _df.columns else 0

    date_min, date_max = '', ''
    if _df is not None and 'date_iso' in _df.columns:
        dates = _df['date_iso'].dropna().sort_values()
        if not dates.empty:
            date_min, date_max = str(dates.iloc[0])[:10], str(dates.iloc[-1])[:10]

    top_wit = sorted(_by_witness.items(), key=lambda x: len(x[1]), reverse=True)[:top_n]
    top_rows = ""
    for w, evts in top_wit:
        top_rows += f"<tr><td>{w}</td><td>{len(evts)}</td></tr>\n"

    place_rows = ""
    if _df is not None and 'place_name' in _df.columns:
        for place, cnt in _df['place_name'].value_counts().head(top_n).items():
            place_rows += f"<tr><td>{place}</td><td>{cnt}</td></tr>\n"

    timeline_rows = ""
    if _df is not None and 'date_iso' in _df.columns:
        _df_t = _df.copy()
        _df_t['year'] = _df_t['date_iso'].astype(str).str[:4]
        _df_t = _df_t[_df_t['year'].str.match(r'^\d{4}$')]
        for year, cnt in _df_t.groupby('year').size().items():
            timeline_rows += f"<tr><td>{year}</td><td>{cnt}</td></tr>\n"

    bridge_section = ""
    try:
        from modules.testigos.analysis import compute_bridge_families as _cbf
        br_df = _cbf(_df)
        if not br_df.empty:
            br_rows = ""
            for _, row in br_df.head(top_n).iterrows():
                br_rows += (
                    f"<tr><td>{row['label']}</td>"
                    f"<td>{row['bridge_index']}</td>"
                    f"<td>{row['n_padrinos']}</td>"
                    f"<td>{row['apellidos_padrinos']}</td></tr>\n"
                )
            bridge_section = f"""
            <h2>Top familias puente</h2>
            <table>
              <thead><tr><th>Familia</th><th>Índice puente</th><th>Padrinos distintos</th><th>Apellidos padrinos</th></tr></thead>
              <tbody>{br_rows}</tbody>
            </table>"""
    except Exception:
        pass

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <title>Informe de red — Genealogía Testigos</title>
  <style>
    body {{font-family: Georgia, serif; max-width: 1100px; margin: 0 auto; padding: 20px; color: #222;}}
    h1 {{color: #4a3728; border-bottom: 2px solid #c8a96e; padding-bottom: 8px;}}
    h2 {{color: #6b4c2a; margin-top: 32px;}}
    table {{border-collapse: collapse; width: 100%; margin-bottom: 24px;}}
    th {{background: #f0e6d3; color: #4a3728; padding: 8px; text-align: left; border: 1px solid #d4b896;}}
    td {{padding: 6px 8px; border: 1px solid #e0cdb5; vertical-align: top;}}
    tr:nth-child(even) {{background: #faf5ef;}}
    .stat-grid {{display: flex; gap: 24px; flex-wrap: wrap; margin-bottom: 24px;}}
    .stat-box {{background: #f9f3ea; border: 1px solid #d4b896; border-radius: 8px; padding: 16px 24px; min-width: 140px;}}
    .stat-box .val {{font-size: 2em; font-weight: bold; color: #4a3728;}}
    .stat-box .lbl {{font-size: 0.85em; color: #888;}}
    .footer {{margin-top: 40px; color: #999; font-size: 12px; border-top: 1px solid #ddd; padding-top: 8px;}}
  </style>
</head>
<body>
  <h1>Informe de red — Genealogía Testigos</h1>
  <div class="stat-grid">
    <div class="stat-box"><div class="val">{total_events}</div><div class="lbl">Total eventos</div></div>
    <div class="stat-box"><div class="val">{unique_witnesses}</div><div class="lbl">Testigos únicos</div></div>
    <div class="stat-box"><div class="val">{unique_places}</div><div class="lbl">Lugares únicos</div></div>
    <div class="stat-box"><div class="val">{date_min}</div><div class="lbl">Primer evento</div></div>
    <div class="stat-box"><div class="val">{date_max}</div><div class="lbl">Último evento</div></div>
  </div>
  <h2>Top {top_n} testigos por apariciones</h2>
  <table><thead><tr><th>Testigo</th><th>Apariciones</th></tr></thead>
  <tbody>{top_rows}</tbody></table>
  <h2>Top {top_n} lugares</h2>
  <table><thead><tr><th>Lugar</th><th>Eventos</th></tr></thead>
  <tbody>{place_rows}</tbody></table>
  <h2>Actividad por año</h2>
  <table><thead><tr><th>Año</th><th>Eventos</th></tr></thead>
  <tbody>{timeline_rows}</tbody></table>
  {bridge_section}
  <div class="footer">Genealogía Testigos &mdash; {report_date}</div>
</body>
</html>"""
    return html


def try_export_pdf(html_str: str):
    """Convierte HTML a PDF. Retorna (bytes, None) si tiene éxito, o (None, str) con el error."""
    try:
        import pdfkit
        return pdfkit.from_string(html_str, False), None
    except ImportError:
        pass
    except Exception as e:
        return None, f"pdfkit: {e}"
    try:
        from weasyprint import HTML as _WHTML
        return _WHTML(string=html_str).write_pdf(), None
    except ImportError:
        pass
    except Exception as e:
        return None, f"weasyprint: {e}"
    return None, None
