"""
The weekly news digest as an email that looks like a newspaper front page:
"The Sa'ei Weekly". Built from news_agent.make_digest()'s items.

Written for mail apps, not browsers: layout is nested tables, every style is
inline (Gmail drops <style> blocks in many cases and ignores external CSS),
fonts are ones every mail app has (Georgia / Times), and there are no images,
so nothing is blocked and nothing needs hosting. A small <style> block only
adds the narrow-screen stacking, which mail apps that drop it simply skip.

Each story's headline is the article's own headline; the text under it is the
summary point that cites it; the source line links to the article. So
nothing in the paper is a headline the model wrote.
"""
from datetime import date
from html import escape

PAPER = "#f7f3ea"
PAGE = "#e9e5dc"
INK = "#1a1a1a"
MUTED = "#5a554d"
ACCENT = "#9c3e1e"
SERIF = "Georgia, 'Times New Roman', Times, serif"
SANS = "Arial, Helvetica, sans-serif"


def _story(item: dict, lead: bool = False) -> str:
    sources = item.get("sources") or []
    first = sources[0] if sources else {}
    headline = escape(first.get("title") or item["text"])
    kicker = escape((first.get("source") or "In the news").upper())
    body = escape(item["text"]) if first.get("title") else ""
    links = " &nbsp;·&nbsp; ".join(
        f'<a href="{escape(s["link"])}" style="color:{ACCENT};text-decoration:none;">'
        f'{"Read the story" if i == 0 else escape(s.get("source") or "More")} &rarr;</a>'
        for i, s in enumerate(s for s in sources if s.get("link")))
    size, line = ("30px", "36px") if lead else ("19px", "25px")
    body_size, body_line = ("17px", "27px") if lead else ("15px", "23px")
    italic = "font-style:italic;" if lead else ""
    body_html = (f'<p style="margin:0 0 10px;font-family:{SERIF};font-size:{body_size};'
                 f'line-height:{body_line};color:{INK};{italic}">{body}</p>') if body else ""
    links_html = (f'<p style="margin:0;font-family:{SANS};font-size:11px;letter-spacing:1px;'
                  f'text-transform:uppercase;">{links}</p>') if links else ""
    return f"""
      <p style="margin:0 0 6px;font-family:{SANS};font-size:11px;letter-spacing:1.5px;color:{ACCENT};font-weight:bold;">{kicker}</p>
      <h2 style="margin:0 0 8px;font-family:{SERIF};font-size:{size};line-height:{line};font-weight:bold;color:{INK};">{headline}</h2>
      {body_html}
      {links_html}"""


def render_digest_email(digest: dict, *, edition: int = 1, today: date | None = None,
                        reader: str = "", app_url: str = "http://localhost:8001/") -> str:
    """The digest as a full HTML email document."""
    today = today or date.today()
    topic = escape(digest.get("topic") or "your field")
    items = digest.get("items") or []
    lead, rest = (items[0], items[1:]) if items else (None, [])

    rule = f'<tr><td style="border-top:1px solid {INK};font-size:0;line-height:0;">&nbsp;</td></tr>'
    double_rule = (f'<tr><td style="border-top:3px double {INK};padding-top:2px;font-size:0;line-height:0;">'
                   f'&nbsp;</td></tr>')

    # Two stories per row, divided by a hairline, like newspaper columns.
    grid_rows = []
    for i in range(0, len(rest), 2):
        left, right = rest[i], rest[i + 1] if i + 1 < len(rest) else None
        if right is None:   # an odd last story takes the full width
            grid_rows.append(f"""
        <tr><td colspan="2" valign="top" style="padding:18px 0;border-bottom:1px solid #cfc8b8;">{_story(left)}</td></tr>""")
            continue
        grid_rows.append(f"""
        <tr>
          <td class="col" width="50%" valign="top" style="padding:18px 16px 18px 0;border-bottom:1px solid #cfc8b8;">{_story(left)}</td>
          <td class="col col-right" width="50%" valign="top" style="padding:18px 0 18px 16px;border-left:1px solid #cfc8b8;border-bottom:1px solid #cfc8b8;">{_story(right)}</td>
        </tr>""")

    cited = []
    for item in items:
        for s in item.get("sources") or []:
            if s.get("link") and s not in cited:
                cited.append(s)
    source_items = []
    for s in cited:
        outlet = (f' &mdash; <span style="color:{MUTED};">{escape(s["source"])}</span>'
                  if s.get("source") else "")
        source_items.append(
            f'<li style="margin:0 0 6px;"><a href="{escape(s["link"])}" '
            f'style="color:{ACCENT};text-decoration:none;">{escape(s.get("title") or s["link"])}</a>{outlet}</li>')
    sources = "".join(source_items)

    empty = "" if items else f"""
      <tr><td style="padding:28px 0;font-family:{SERIF};font-size:17px;line-height:27px;color:{INK};text-align:center;">
        {escape(digest.get("text") or "No news this week.")}
      </td></tr>"""

    lead_html = (f'<tr><td style="padding:10px 0 20px;border-bottom:1px solid {INK};">'
                 f'{_story(lead, lead=True)}</td></tr>') if lead else ""
    grid_html = (f'<tr><td><table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
                 f'{"".join(grid_rows)}</table></td></tr>') if grid_rows else ""
    sources_html = (f'<tr><td style="padding:22px 0 0;">'
                    f'<p style="margin:0 0 8px;font-family:{SANS};font-size:11px;letter-spacing:2px;'
                    f'font-weight:bold;color:{INK};">SOURCES</p>'
                    f'<ol style="margin:0;padding-left:20px;font-family:{SERIF};font-size:13px;'
                    f'line-height:20px;color:{INK};">{sources}</ol></td></tr>') if sources else ""

    weekday = today.strftime("%A").upper()
    dateline = f"{weekday}, {today.day} {today.strftime('%B %Y').upper()}"
    greeting = f"Compiled for {escape(reader)} by Sa'ei" if reader else "Compiled by Sa'ei"

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>The Sa'ei Weekly</title>
<style>
  @media only screen and (max-width: 620px) {{
    .sheet {{ padding: 20px 18px !important; }}
    .col {{ display: block !important; width: 100% !important; padding: 16px 0 !important; border-left: 0 !important; }}
    .masthead {{ font-size: 40px !important; line-height: 46px !important; }}
  }}
</style></head>
<body style="margin:0;padding:0;background:{PAGE};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{PAGE};">
<tr><td align="center" style="padding:24px 12px;">
  <table role="presentation" width="640" cellpadding="0" cellspacing="0" style="width:100%;max-width:640px;background:{PAPER};">
  <tr><td class="sheet" style="padding:28px 36px 32px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
      {rule}
      <tr><td style="padding:6px 0;font-family:{SANS};font-size:10px;letter-spacing:1.5px;color:{INK};">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
          <td align="left" style="font-family:{SANS};font-size:10px;letter-spacing:1.5px;">VOL. 1 &middot; NO. {edition}</td>
          <td align="center" style="font-family:{SANS};font-size:10px;letter-spacing:1.5px;">{dateline}</td>
          <td align="right" style="font-family:{SANS};font-size:10px;letter-spacing:1.5px;">WEEKLY</td>
        </tr></table>
      </td></tr>
      {rule}
      <tr><td align="center" style="padding:18px 0 4px;">
        <span class="masthead" style="font-family:{SERIF};font-size:54px;line-height:60px;font-weight:bold;color:{INK};letter-spacing:-1px;">The Sa&#8217;ei Weekly</span>
        <span lang="ar" dir="rtl" style="font-family:{SERIF};font-size:20px;color:{ACCENT};padding-left:8px;">&#1587;&#1575;&#1593;&#1610;</span>
      </td></tr>
      <tr><td align="center" style="padding:0 0 14px;font-family:{SERIF};font-size:15px;font-style:italic;color:{MUTED};">
        The week in your field, in two minutes.
      </td></tr>
      {double_rule}
      <tr><td style="padding:16px 0 4px;font-family:{SANS};font-size:11px;letter-spacing:2px;font-weight:bold;color:{ACCENT};">
        THIS WEEK IN &middot; {topic.upper()}
      </td></tr>
      {empty}
      {lead_html}
      {grid_html}
      {sources_html}
      <tr><td style="padding-top:24px;">&nbsp;</td></tr>
      {rule}
      <tr><td align="center" style="padding:12px 0 4px;font-family:{SERIF};font-size:13px;line-height:20px;font-style:italic;color:{MUTED};">
        {greeting} from this week's Google News headlines. Summaries stick to what the headlines say &mdash; open a story for the details.
      </td></tr>
      <tr><td align="center" style="padding:4px 0 12px;font-family:{SANS};font-size:11px;letter-spacing:1px;">
        <a href="{escape(app_url)}settings.html#email" style="color:{ACCENT};text-decoration:none;">CHANGE YOUR NEWS TOPIC</a>
        &nbsp;&middot;&nbsp;
        <a href="{escape(app_url)}" style="color:{ACCENT};text-decoration:none;">OPEN SA&#8217;EI</a>
      </td></tr>
      {rule}
      <tr><td align="center" style="padding:10px 0 0;font-family:{SERIF};font-size:12px;font-style:italic;color:{MUTED};">
        &ldquo;Big dreams aren&#8217;t reached in a single stride &mdash; the caravan advances step by deliberate step.&rdquo;
      </td></tr>
    </table>
  </td></tr>
  </table>
</td></tr>
</table>
</body></html>"""
