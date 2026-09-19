"""Hand-written HTML for the fake servicing console.

Deliberately hostile, the way a 2003-era vendor app is hostile:

- a frameset, so the interesting controls are not on the top-level document
- table-based layout, <font> tags, no semantic elements, no landmarks
- generated ids like ctl00_r3_c2 that look stable but are positional
- no data-testid anywhere, and no <label for=...> associations
- navigation through inline onclick handlers rather than real links

Accessible names are mixed on purpose, because real legacy apps are mixed:
submit buttons get a name from their value, a few inputs carry a title
attribute, and the rest are nameless. That mix is what forces the locator
strategy to have a fallback chain instead of assuming role+name always works.
"""

_SHELL = """<html>
<head><title>{title}</title></head>
<body bgcolor="#d4d0c8">
<table width="100%" border="0" cellpadding="2" cellspacing="0">
<tr><td bgcolor="#003366">
<font color="#ffffff" face="Verdana" size="2"><b>NORTHGATE CU &mdash; SERVICING CONSOLE</b></font>
</td></tr>
</table>
<br>
{body}
</body>
</html>
"""


def _banner(message: str) -> str:
    """Renders the red message line some screens show above their form."""
    if not message:
        return ""
    return f'<font face="Verdana" size="1" color="#cc0000">{message}</font><br><br>'


def _page(title: str, body: str) -> str:
    """Wraps body HTML in the app's standard chrome."""
    return _SHELL.format(title=title, body=body)


def login_screen(message: str = "") -> str:
    """Sign-on screen. Neither input has a label or a title, so both are nameless."""
    banner = _banner(message)
    body = f"""{banner}<form method="post" action="/login">
<table border="0" cellpadding="4" cellspacing="0">
<tr>
  <td><font face="Verdana" size="1">Operator ID</font></td>
  <td><input type="text" name="operator" id="ctl00_r1_c2" size="24"></td>
</tr>
<tr>
  <td><font face="Verdana" size="1">Password</font></td>
  <td><input type="password" name="password" id="ctl00_r2_c2" size="24"></td>
</tr>
<tr>
  <td colspan="2"><input type="submit" id="ctl00_r3_c1" value="Sign On"></td>
</tr>
</table>
</form>"""
    return _page("Sign On", body)


def console_frameset() -> str:
    """The console shell. Everything useful lives inside the content frame."""
    return """<html>
<head><title>Servicing Console</title></head>
<frameset cols="170,*" border="1">
  <frame name="navframe" src="/nav">
  <frame name="contentframe" src="/search">
</frameset>
</html>
"""


def nav_frame() -> str:
    """Left-hand menu. Navigates through inline onclick, not real links."""
    body = """<table border="0" cellpadding="3" cellspacing="0">
<tr><td><font face="Verdana" size="1"><b>Menu</b></font></td></tr>
<tr><td onclick="parent.contentframe.location='/search'" style="cursor:pointer">
  <font face="Verdana" size="1" color="#003366"><u>Member Search</u></font></td></tr>
<tr><td onclick="alert('Not available in this environment')" style="cursor:pointer">
  <font face="Verdana" size="1" color="#003366"><u>Transactions</u></font></td></tr>
<tr><td onclick="alert('Not available in this environment')" style="cursor:pointer">
  <font face="Verdana" size="1" color="#003366"><u>Reports</u></font></td></tr>
</table>"""
    return _page("Menu", body)


def search_screen(message: str = "") -> str:
    """Member lookup form. The input carries a title, so it does have a name."""
    banner = _banner(message)
    body = f"""{banner}<form method="post" action="/search">
<table border="0" cellpadding="4" cellspacing="0">
<tr>
  <td><font face="Verdana" size="1">Member Number</font></td>
  <td><input type="text" name="member" id="ctl00_r1_c2" size="16" title="Member Number"></td>
  <td><input type="submit" id="ctl00_r1_c3" value="Search"></td>
</tr>
</table>
</form>"""
    return _page("Member Search", body)


def results_screen(number: str, name: str) -> str:
    """Search results. One hit, in a table row that has to be clicked to drill in."""
    body = f"""<font face="Verdana" size="1">1 record found.</font><br><br>
<table border="1" cellpadding="4" cellspacing="0" bgcolor="#ffffff">
<tr bgcolor="#c0c0c0">
  <td><font face="Verdana" size="1"><b>Member No</b></font></td>
  <td><font face="Verdana" size="1"><b>Name</b></font></td>
  <td><font face="Verdana" size="1"><b>&nbsp;</b></font></td>
</tr>
<tr>
  <td><font face="Verdana" size="1">{number}</font></td>
  <td><font face="Verdana" size="1">{name}</font></td>
  <td><input type="button" id="ctl00_r2_c3" value="Open"
       onclick="location.href='/member/{number}'"></td>
</tr>
</table>"""
    return _page("Search Results", body)


def consent_screen(number: str) -> str:
    """The interstitial that interrupts some lookups - a recoverable condition."""
    body = f"""<table border="1" cellpadding="8" cellspacing="0" bgcolor="#ffffcc">
<tr><td>
<font face="Verdana" size="1"><b>Consent confirmation required</b><br><br>
This member has elected paper-only disclosures. Confirm verbal consent before
viewing account detail.</font><br><br>
<input type="button" id="ctl00_r1_c1" value="Confirm Consent"
   onclick="location.href='/member/{number}?consented=1'">
</td></tr>
</table>"""
    return _page("Consent Required", body)


def denied_screen() -> str:
    """Permission denial - a legitimate business outcome, not a failure."""
    body = """<table border="1" cellpadding="8" cellspacing="0" bgcolor="#ffe0e0">
<tr><td><font face="Verdana" size="1"><b>Access denied</b><br><br>
Your operator profile is not entitled to view this member record.
Contact your branch administrator.</font></td></tr>
</table>"""
    return _page("Access Denied", body)


def _account_rows(accounts: list[tuple[str, str, str]]) -> str:
    """Renders the account table rows for the detail screen."""
    cells = (
        f'<tr><td><font face="Verdana" size="1">{kind}</font></td>'
        f'<td><font face="Verdana" size="1">{number}</font></td>'
        f'<td align="right"><font face="Verdana" size="1">{balance}</font></td></tr>'
        for kind, number, balance in accounts
    )
    return "".join(cells)


def member_screen(number: str, name: str, accounts: list[tuple[str, str, str]]) -> str:
    """Member detail. The balance sits in an unlabelled cell of a nested table."""
    body = f"""<table border="0" cellpadding="2" cellspacing="0">
<tr><td><font face="Verdana" size="1">Member No</font></td>
    <td><font face="Verdana" size="1"><b>{number}</b></font></td></tr>
<tr><td><font face="Verdana" size="1">Name</font></td>
    <td><font face="Verdana" size="1"><b>{name}</b></font></td></tr>
</table>
<br>
<table border="1" cellpadding="4" cellspacing="0" bgcolor="#ffffff">
<tr bgcolor="#c0c0c0">
  <td><font face="Verdana" size="1"><b>Account Type</b></font></td>
  <td><font face="Verdana" size="1"><b>Suffix</b></font></td>
  <td><font face="Verdana" size="1"><b>Current Balance</b></font></td>
</tr>
{_account_rows(accounts)}
</table>
<br>
<input type="button" id="ctl00_r9_c1" value="Back to Search"
   onclick="location.href='/search'">"""
    return _page("Member Detail", body)


def error_screen() -> str:
    """The app's own unhandled-error page - a hard failure for the caller."""
    body = """<table border="1" cellpadding="8" cellspacing="0" bgcolor="#ffe0e0">
<tr><td><font face="Verdana" size="1"><b>Application error</b><br><br>
An unexpected error occurred. Reference NGCU-500. Please contact support.
</font></td></tr>
</table>"""
    return _page("Application Error", body)
