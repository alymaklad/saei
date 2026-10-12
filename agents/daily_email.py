"""
The evening report as a designed email: what Sa'ei did in the last 24 hours.
Built from jobs/daily_report.collect_daily_report()'s dict; the layout was
drawn in Stitch ("Sa'ei Daily Report Email").

Same rules as news_email.py: nested tables, inline styles, system fonts, no
images -- the bars and the week chart are table cells with a background.
"""
from datetime import datetime
from html import escape

PAGE = "#ebe3d7"
SHEET = "#f3ede3"
BAND = "#f8f5ef"
INK = "#22201c"
MUTED = "#6b655b"
LINE = "#e2d9cb"
TEAL = "#2f6f6a"
TEAL_TINT = "#e1eeec"
AMBER = "#b7791f"
AMBER_TINT = "#fbf1dc"
TRACK = "#e4dbcd"
SOURCE_COLORS = ("#2f6f6a", "#5c8f8a", "#8fb5b0", "#cbbfae", "#ddd3c3", "#efe8dc")
SERIF = "Georgia, 'Times New Roman', Times, serif"
SANS = "Arial, Helvetica, sans-serif"

STATUS_LABELS = {
    "pending_review": "Pending review",
    "cv_rewritten_notify_user": "Tailored",
    "auto_submitted": "Sent automatically",
    "would_apply": "Would apply (dry run)",
    "sent": "Emailed",
}
SET_ASIDE_REASONS = {
    "unreadable": "the AI's answer couldn't be read",
    "limit": "the AI's usage limit ran out",
}


def _pct(score) -> str:
    return f"{round(score * 100)}%" if score is not None else "–"


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _delta(n: int) -> str:
    if n == 0:
        return f'<span style="color:{MUTED};font-weight:normal;">Same as yesterday</span>'
    sign = "+" if n > 0 else "&minus;"
    return f"{sign}{abs(n)} vs yesterday"


def headline(data: dict) -> str:
    """The one-sentence summary at the top (also the plain-text first line)."""
    if not data["found"] and not data["scored"]:
        return "A quiet day: no new roles came in."
    parts = [_plural(data["found"], "new role")+ " found"]
    if data["strong"]:
        parts.append(_plural(data["strong"], "strong match", "strong matches"))
    ready = len(data["needs_you"])
    if ready:
        parts.append(f"{_plural(ready, 'application')} ready for you")
    elif data["sent"]:
        parts.append(f"{data['sent']} sent")
    text = ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1]
    return text[0].upper() + text[1:] + "."


def _section(title: str, intro: str, body: str, band: bool = False) -> str:
    intro_html = (f'<p style="margin:2px 0 14px;font-family:{SANS};font-size:13px;line-height:19px;'
                  f'color:{MUTED};">{intro}</p>') if intro else '<div style="height:12px;"></div>'
    return f"""
  <tr><td class="pad" style="padding:26px 36px;border-top:1px solid {LINE};{'background:' + BAND + ';' if band else ''}">
    <p style="margin:0;font-family:{SERIF};font-size:19px;line-height:25px;color:{INK};">{title}</p>
    {intro_html}
    {body}
  </td></tr>"""


def _stats(data: dict) -> str:
    sent_label = "Would send" if data["dry_run"] else "Sent"
    figures = [
        (data["found"], "Roles found", _delta(data["found_delta"])),
        (data["scored"], "Scored", _delta(data["scored_delta"])),
        (data["tailored"], "CVs tailored", ""),
        (data["sent"], sent_label, ""),
    ]
    cells = "".join(
        f'<td class="stat" width="25%" valign="top" style="padding:0 8px 0 0;">'
        f'<p style="margin:0;font-family:{SERIF};font-size:30px;line-height:36px;font-weight:bold;color:{INK};">{n}</p>'
        f'<p style="margin:2px 0 0;font-family:{SANS};font-size:13px;color:{INK};">{label}</p>'
        f'<p style="margin:2px 0 0;font-family:{SANS};font-size:11px;font-weight:bold;color:{TEAL};">{delta}</p></td>'
        for n, label, delta in figures)
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>{cells}</tr></table>'


def _ready_card(role: dict, app_url: str) -> str:
    meta = " &middot; ".join(escape(x) for x in (role["company"], f"via {role['board']}") if x)
    if role["status"] == "cv_rewritten_notify_user":
        note = f"Tailored CV ready &middot; CV fit {_pct(role['ats'])} &rarr; {_pct(role['tailored_ats'])}"
    else:
        note = f"Draft ready &middot; CV fit {_pct(role['ats'])}"
    if role.get("apply_email"):
        note += f" &middot; Send to {escape(role['apply_email'])}"
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 12px;background:{SHEET};border:1px solid {LINE};border-left:4px solid {AMBER};">
    <tr>
      <td class="col" valign="middle" style="padding:14px 16px;">
        <span style="display:inline-block;padding:2px 8px;border:1px solid #e6c98f;border-radius:10px;background:{AMBER_TINT};font-family:{SANS};font-size:11px;font-weight:bold;color:#8a5a12;">{_pct(role['match'])} match</span>
        <p style="margin:8px 0 2px;font-family:{SANS};font-size:15px;line-height:20px;font-weight:bold;color:{INK};">{escape(role['title'])}</p>
        <p style="margin:0;font-family:{SANS};font-size:13px;line-height:18px;color:{MUTED};">{meta}</p>
        <p style="margin:4px 0 0;font-family:{SANS};font-size:12px;line-height:17px;color:{TEAL};">{note}</p>
      </td>
      <td class="col" valign="middle" align="right" style="padding:14px 16px;white-space:nowrap;">
        <a href="{escape(app_url)}applications.html" style="display:inline-block;padding:9px 14px;background:{AMBER};border-radius:3px;font-family:{SANS};font-size:13px;font-weight:bold;color:#ffffff;text-decoration:none;">Review and send</a>
      </td>
    </tr></table>"""


def _chip(text: str, strong: bool = False) -> str:
    bg, fg = (TEAL_TINT, TEAL) if strong else ("#ebe4d8", MUTED)
    return (f'<span style="display:inline-block;padding:2px 7px;border-radius:9px;background:{bg};'
            f'font-family:{SANS};font-size:11px;font-weight:bold;color:{fg};">{text}</span>')


def _match_row(role: dict, last: bool) -> str:
    meta = " &middot; ".join(escape(x) for x in (role["company"], role["board"]) if x)
    fit = f"CV fit {_pct(role['ats'])}"
    if role["tailored_ats"] is not None:
        fit += f" &rarr; {_pct(role['tailored_ats'])}"
    status = "Emailed" if role["email_sent"] else STATUS_LABELS.get(role["status"], (role["status"] or "").replace("_", " ").capitalize())
    border = "" if last else f"border-bottom:1px solid {LINE};"
    link = (f'<a href="{escape(role["url"])}" style="font-family:{SANS};font-size:13px;font-weight:bold;'
            f'color:{TEAL};text-decoration:none;white-space:nowrap;">View posting</a>') if role.get("url") else ""
    return f"""
    <tr>
      <td valign="top" style="padding:14px 0;{border}">
        <p style="margin:0 0 2px;font-family:{SANS};font-size:15px;line-height:20px;font-weight:bold;color:{INK};">{escape(role['title'])}</p>
        <p style="margin:0 0 7px;font-family:{SANS};font-size:13px;line-height:18px;color:{MUTED};">{meta}</p>
        {_chip(_pct(role['match']) + ' match', strong=True)}
        <span style="font-family:{SANS};font-size:12px;color:{MUTED};padding:0 6px;">{fit}</span>
        {_chip(escape(status), strong=status in ('Emailed', 'Sent automatically'))}
      </td>
      <td valign="middle" align="right" style="padding:14px 0 14px 12px;{border}">{link}</td>
    </tr>"""


def _skills(data: dict, app_url: str) -> str:
    top = data["skills"][0][1] if data["skills"] else 1
    rows = []
    for skill, count in data["skills"]:
        width = max(4, round(count / top * 70))
        rows.append(f"""
    <tr><td style="padding:0 0 10px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
        <td style="font-family:{SANS};font-size:13px;line-height:18px;color:{INK};">{escape(skill)}</td>
        <td align="right" style="font-family:{SANS};font-size:12px;color:{MUTED};white-space:nowrap;">{_plural(count, 'role')}</td>
      </tr></table>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:4px;"><tr>
        <td width="{width}%" height="6" style="background:{TEAL};font-size:0;line-height:0;">&nbsp;</td>
        <td height="6" style="background:{TRACK};font-size:0;line-height:0;">&nbsp;</td>
      </tr></table>
    </td></tr>""")
    tip = (f'<p style="margin:6px 0 0;padding:12px 14px;border:1px dashed #d4c8b6;background:{BAND};'
           f'font-family:{SANS};font-size:13px;line-height:19px;color:{INK};"><b>Tip:</b> Used one of these in a '
           f'job or project? <a href="{escape(app_url)}profile.html" style="color:{TEAL};font-weight:bold;">'
           f'Add it to your profile</a> and it counts the next time roles are scored.</p>')
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{"".join(rows)}</table>{tip}'


def _sources(data: dict) -> str:
    total = sum(n for _, n in data["sources"]) or 1
    shown = data["sources"][:5]
    rest = sum(n for _, n in data["sources"][5:])
    if rest:
        shown = shown + [("Other boards", rest)]
    bar = "".join(
        f'<td width="{max(1, round(n / total * 100))}%" height="10" style="background:{SOURCE_COLORS[i]};'
        f'font-size:0;line-height:0;">&nbsp;</td>' for i, (_, n) in enumerate(shown))
    legend = " ".join(
        f'<span style="display:inline-block;margin:0 16px 6px 0;font-family:{SANS};font-size:12px;color:{INK};'
        f'white-space:nowrap;"><span style="display:inline-block;width:9px;height:9px;background:{SOURCE_COLORS[i]};'
        f'margin-right:6px;"></span>{escape(name)} ({n})</span>' for i, (name, n) in enumerate(shown))
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-radius:5px;">'
            f'<tr>{bar}</tr></table><p style="margin:12px 0 0;">{legend}</p>')


def _week(data: dict) -> str:
    peak = max((d["count"] for d in data["week"]), default=0) or 1
    today = data["now"].date()
    cells = []
    for d in data["week"]:
        is_today = d["day"] == today
        height = max(3, round(d["count"] / peak * 48))
        colour = TEAL if is_today else ("#8fb5b0" if d["count"] == peak and d["count"] else "#d6cbba")
        label = "Today" if is_today else d["day"].strftime("%a")
        weight = "bold" if is_today else "normal"
        cells.append(
            f'<td align="center" valign="bottom" width="14%" style="padding:0 4px;">'
            f'<p style="margin:0 0 4px;font-family:{SANS};font-size:11px;font-weight:{weight};color:{TEAL if is_today else MUTED};">{d["count"]}</p>'
            f'<table role="presentation" width="60%" cellpadding="0" cellspacing="0"><tr>'
            f'<td height="{height}" style="background:{colour};font-size:0;line-height:0;">&nbsp;</td></tr></table>'
            f'<p style="margin:6px 0 0;font-family:{SANS};font-size:12px;font-weight:{weight};color:{INK if is_today else MUTED};">{label}</p></td>')
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:6px;">'
            f'<tr>{"".join(cells)}</tr></table>')


def render_daily_email(data: dict, *, reader: str = "", app_url: str = "http://localhost:8001/") -> str:
    """The daily report as a full HTML email document."""
    now: datetime = data["now"]
    dateline = f"{now.strftime('%A')}, {now.day} {now.strftime('%B %Y')}"
    first_name = (reader or "").split()[0] if (reader or "").strip() else ""
    greeting = f"Good evening, {escape(first_name)}. Here's today's search." if first_name else "Good evening. Here's today's search."

    sections = [_section("Today in numbers", "", _stats(data), band=False)]

    if data["needs_you"]:
        ready = data["needs_you"][:3]
        more = len(data["needs_you"]) - len(ready)
        intro = (f"{_plural(len(data['needs_you']), 'application')} from today "
                 f"{'is' if len(data['needs_you']) == 1 else 'are'} waiting for your review.")
        if data["waiting_older"]:
            intro += f" {data['waiting_older']} more from earlier this week too."
        body = "".join(_ready_card(r, app_url) for r in ready)
        if more:
            body += (f'<p style="margin:4px 0 0;font-family:{SANS};font-size:13px;">'
                     f'<a href="{escape(app_url)}applications.html" style="color:{TEAL};font-weight:bold;'
                     f'text-decoration:none;">See all {len(data["needs_you"])} on the Applications page</a></p>')
        sections.append(_section("Needs your attention", intro, body, band=True))

    if data["best"]:
        rows = "".join(_match_row(r, i == len(data["best"]) - 1) for i, r in enumerate(data["best"]))
        sections.append(_section(
            "Today's best matches", "Ranked by how well each role fits your profile.",
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table>'))

    if data["skills"]:
        sections.append(_section(
            "Skills that kept coming up",
            f"Asked for by today's roles but missing from your CV ({_plural(data['skill_roles'], 'role')} checked).",
            _skills(data, app_url)))

    if data["sources"]:
        sections.append(_section(
            "Where today's roles came from",
            f"{_plural(data['found'], 'new posting')} across your job boards.", _sources(data)))

    if data["emails"]:
        items = "".join(
            f'<li style="margin:0 0 6px;">{escape(e["subject"] or "Application")} '
            f'<span style="color:{MUTED};">to {escape(e["to"] or "")}'
            f'{" (dry run)" if e["status"] == "dry_run" else " (failed)" if e["status"] == "failed" else ""}</span></li>'
            for e in data["emails"][:6])
        sections.append(_section(
            "Emails sent for you", "",
            f'<ul style="margin:0;padding-left:18px;font-family:{SANS};font-size:13px;line-height:19px;color:{INK};">{items}</ul>'))

    if data["set_aside"]:
        notes = "; ".join(f"{_plural(n, 'role')} because {SET_ASIDE_REASONS.get(reason, reason)}"
                          for reason, n in data["set_aside"].items())
        sections.append(f"""
  <tr><td class="pad" style="padding:20px 36px;border-top:1px solid {LINE};background:{BAND};">
    <p style="margin:0;font-family:{SERIF};font-size:16px;color:{INK};">Carried to tomorrow</p>
    <p style="margin:4px 0 0;font-family:{SANS};font-size:13px;line-height:19px;color:{MUTED};">Set aside: {notes}. They'll be scored first in the next search.</p>
  </td></tr>""")

    week_total = sum(d["count"] for d in data["week"])
    sections.append(f"""
  <tr><td class="pad" style="padding:26px 36px;border-top:1px solid {LINE};">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td><p style="margin:0;font-family:{SERIF};font-size:19px;line-height:25px;color:{INK};">This week so far</p>
          <p style="margin:2px 0 0;font-family:{SANS};font-size:13px;color:{MUTED};">Roles scored each day, last 7 days.</p></td>
      <td align="right" valign="top" style="font-family:{SANS};font-size:13px;color:{MUTED};white-space:nowrap;">Total: <b style="font-family:{SERIF};font-size:17px;color:{INK};">{week_total}</b></td>
    </tr></table>
    {_week(data)}
  </td></tr>""")

    dry = (f'<tr><td class="pad" style="padding:10px 36px;background:{AMBER_TINT};font-family:{SANS};font-size:12px;'
           f'color:#8a5a12;">Dry run is on: nothing was actually sent today.</td></tr>') if data["dry_run"] else ""

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sa'ei daily report</title>
<style>
  @media only screen and (max-width: 620px) {{
    .pad {{ padding-left: 20px !important; padding-right: 20px !important; }}
    .col {{ display: block !important; width: auto !important; text-align: left !important; }}
    .stat {{ display: inline-block !important; width: 48% !important; padding-bottom: 14px !important; }}
  }}
</style></head>
<body style="margin:0;padding:0;background:{PAGE};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{PAGE};">
<tr><td align="center" style="padding:24px 12px;">
  <table role="presentation" width="640" cellpadding="0" cellspacing="0" style="width:100%;max-width:640px;background:{SHEET};border:1px solid {LINE};">
  <tr><td class="pad" style="padding:30px 36px 22px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td style="font-family:{SANS};font-size:13px;color:{MUTED};">
        <span style="font-family:{SERIF};font-size:20px;font-weight:bold;color:{TEAL};">Sa&#8217;ei</span>
        <span lang="ar" dir="rtl" style="font-family:{SERIF};font-size:13px;color:{MUTED};padding:0 4px;">&#1587;&#1575;&#1593;&#1610;</span>
        &middot; Daily report</td>
      <td align="right" style="font-family:{SANS};font-size:13px;color:{MUTED};">{dateline}</td>
    </tr></table>
    <p style="margin:14px 0 0;font-family:{SANS};font-size:14px;color:{INK};">{greeting}</p>
  </td></tr>
  {dry}
  <tr><td class="pad" style="padding:26px 36px;border-top:1px solid {LINE};background:{BAND};">
    <p style="margin:0;font-family:{SERIF};font-size:25px;line-height:33px;color:{INK};">{escape(headline(data))}</p>
  </td></tr>
  {"".join(sections)}
  <tr><td align="center" class="pad" style="padding:30px 36px 28px;border-top:1px solid {LINE};">
    <a href="{escape(app_url)}" style="display:inline-block;padding:12px 28px;background:{TEAL};border-radius:3px;font-family:{SANS};font-size:15px;font-weight:bold;color:#ffffff;text-decoration:none;">Open Sa&#8217;ei</a>
    <p style="margin:14px 0 22px;font-family:{SANS};font-size:12px;"><a href="{escape(app_url)}settings.html#email" style="color:{MUTED};">Change report settings</a></p>
    <p style="margin:0 0 6px;font-family:{SERIF};font-size:13px;color:#b9ad9a;letter-spacing:2px;">&#8212;&#8212;&#8212; &#9650; &#8212;&#8212;&#8212;</p>
    <p style="margin:0 0 10px;font-family:{SERIF};font-size:14px;font-style:italic;color:{MUTED};">&ldquo;The caravan advances step by deliberate step.&rdquo;</p>
    <p style="margin:0;font-family:{SANS};font-size:11px;line-height:16px;color:#9a917f;">Sent by Sa&#8217;ei every evening at 20:00 while it&#8217;s running.</p>
  </td></tr>
  </table>
</td></tr>
</table>
</body></html>"""
