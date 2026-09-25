"""
OFX client — handles communication with bank OFX servers.
Builds OFX request documents, parses responses, executes transfers.
"""

import uuid
import datetime
from io import BytesIO
from typing import Optional

import requests
from ofxtools.header import make_header
from ofxtools.models import *
from ofxtools.models.bank.stmt import BANKACCTFROM, BANKACCTTO
from ofxtools.models.bank.wire import WIRERQ, WIRERS, EXTBANKDESC
from ofxtools.utils import UTC
from ofxtools.Parser import OFXTree
import xml.etree.ElementTree as ET

from config import BANK_PROFILES
from rich.console import Console
from rich.table import Table

console = Console()


def _generate_trnuid() -> str:
    return str(uuid.uuid4()).replace("-", "").upper()[:32]


def _now_dt():
    return datetime.datetime.now(UTC)


def _build_signon(profile: dict, username: str, password: str) -> SONRQ:
    """Build OFX signon request message."""
    if not username or not password:
        raise ValueError(
            f"Login credentials (username/password) are missing or empty for {profile['name']}.\n"
            f"To query balances or accounts, you must configure logins: python main.py setup <bank>\n"
            f"To use this bank only as an ACH destination, add it as a payee: python main.py add-payee <name> <routing> <account>"
        )
    return SONRQ(
        dtclient=_now_dt(),
        userid=username,
        userpass=password,
        language="ENG",
        fi=FI(org=profile["org"], fid=profile["fid"]),
        appid=profile["app_id"],
        appver=profile["app_ver"],
    )


def _post_ofx(profile: dict, ofx_data: str) -> bytes:
    """POST raw OFX to the bank server and return response bytes."""
    headers = {
        "Content-Type": "application/x-ofx",
        "Accept": "*/*, application/x-ofx",
        "User-Agent": "InetClntApp/3.0",
        "Connection": "close",
    }
    encoded = ofx_data.encode("ascii")
    headers["Content-Length"] = str(len(encoded))

    last_exc = None
    for attempt in range(3):
        try:
            resp = requests.post(
                profile["ofx_url"],
                data=encoded,
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.content
        except requests.exceptions.ConnectionError as e:
            last_exc = e
            import time; time.sleep(1 + attempt)
    raise last_exc


def get_accounts(bank_key: str, username: str, password: str) -> list[dict]:
    """Query the bank for available accounts."""
    profile = BANK_PROFILES[bank_key]

    signon = _build_signon(profile, username, password)
    signup_rq = ACCTINFOTRNRQ(
        trnuid=_generate_trnuid(),
        acctinforq=ACCTINFORQ(dtacctup=datetime.datetime(1970, 1, 1, tzinfo=UTC)),
    )

    signonmsgs = SIGNONMSGSRQV1(sonrq=signon)
    signupmsgs = SIGNUPMSGSRQV1(signup_rq)
    ofx = OFX(signonmsgsrqv1=signonmsgs, signupmsgsrqv1=signupmsgs)

    ofx_str = str(make_header(version=profile["ofx_version"])) + ET.tostring(ofx.to_etree(), encoding="unicode")
    raw_resp = _post_ofx(profile, ofx_str)

    # Write raw response to debug file
    with open("debug_response.xml", "wb") as f:
        f.write(raw_resp)

    parser = OFXTree()
    parser.parse(BytesIO(raw_resp))
    ofx_resp = parser.convert()

    # Check Signon status
    if hasattr(ofx_resp, "signonmsgsrsv1") and ofx_resp.signonmsgsrsv1:
        sonrs = ofx_resp.signonmsgsrsv1.sonrs
        if sonrs.status.code != 0:
            raise Exception(f"Signon failed (Code: {sonrs.status.code}, Message: {sonrs.status.message})")

    accounts = []
    if hasattr(ofx_resp, "signupmsgsrsv1") and ofx_resp.signupmsgsrsv1:
        for trnrs in ofx_resp.signupmsgsrsv1:
            if trnrs.status.code != 0:
                raise Exception(f"Account query transaction failed (Code: {trnrs.status.code}, Message: {trnrs.status.message})")
            if hasattr(trnrs, "acctinfors") and trnrs.acctinfors:
                for acctinfo in trnrs.acctinfors.acctinfo:
                    acct = {
                        "name": getattr(acctinfo, "desc", "N/A"),
                        "type": "unknown",
                    }
                    if hasattr(acctinfo, "bankacctinfo"):
                        bai = acctinfo.bankacctinfo
                        acct["acctid"] = bai.bankacctfrom.acctid
                        acct["routing"] = bai.bankacctfrom.bankid
                        acct["type"] = bai.bankacctfrom.accttype
                    accounts.append(acct)
    return accounts


def get_balance(bank_key: str, username: str, password: str, account_id: str,
                routing_number: str = "", acct_type: str = "CHECKING") -> dict:
    """Fetch account balance via OFX statement request."""
    profile = BANK_PROFILES[bank_key]

    signon = _build_signon(profile, username, password)

    bankacctfrom = BANKACCTFROM(
        bankid=routing_number or profile.get("fid", ""),
        acctid=account_id,
        accttype=acct_type.upper(),
    )

    stmtrq = STMTRQ(
        bankacctfrom=bankacctfrom,
        inctran=INCTRAN(dtstart=datetime.datetime(2020, 1, 1, tzinfo=UTC), include=False),
    )

    stmttrnrq = STMTTRNRQ(trnuid=_generate_trnuid(), stmtrq=stmtrq)
    signonmsgs = SIGNONMSGSRQV1(sonrq=signon)
    bankmsgs = BANKMSGSRQV1(stmttrnrq)
    ofx = OFX(signonmsgsrqv1=signonmsgs, bankmsgsrqv1=bankmsgs)

    ofx_str = str(make_header(version=profile["ofx_version"])) + ET.tostring(ofx.to_etree(), encoding="unicode")
    raw_resp = _post_ofx(profile, ofx_str)

    # Write raw response to debug file
    with open("debug_response.xml", "wb") as f:
        f.write(raw_resp)

    parser = OFXTree()
    parser.parse(BytesIO(raw_resp))
    ofx_resp = parser.convert()

    # Check Signon status
    if hasattr(ofx_resp, "signonmsgsrsv1") and ofx_resp.signonmsgsrsv1:
        sonrs = ofx_resp.signonmsgsrsv1.sonrs
        if sonrs.status.code != 0:
            raise Exception(f"Signon failed (Code: {sonrs.status.code}, Message: {sonrs.status.message})")

    result = {"account_id": account_id, "available": 0.0, "ledger": 0.0}
    if hasattr(ofx_resp, "bankmsgsrsv1") and ofx_resp.bankmsgsrsv1:
        for trnrs in ofx_resp.bankmsgsrsv1:
            if trnrs.status.code != 0:
                raise Exception(f"Statement transaction failed (Code: {trnrs.status.code}, Message: {trnrs.status.message})")
            if hasattr(trnrs, "stmtrs") and trnrs.stmtrs:
                bal = trnrs.stmtrs.availbal
                if bal:
                    result["available"] = float(bal.balamt)
                ledger = trnrs.stmtrs.ledgerbal
                if ledger:
                    result["ledger"] = float(ledger.balamt)
    return result


def send_intrabank_transfer(bank_key: str, username: str, password: str,
                            from_account: str, to_account: str, amount: float,
                            memo: str = "", routing: str = "",
                            from_type: str = "CHECKING", to_type: str = "CHECKING") -> dict:
    """Execute an intrabank funds transfer via OFX INTRATRNRQ."""
    profile = BANK_PROFILES[bank_key]
    signon = _build_signon(profile, username, password)
    rid = routing or profile.get("fid", "")

    bankacctfrom = BANKACCTFROM(bankid=rid, acctid=from_account, accttype=from_type.upper())
    bankacctto = BANKACCTTO(bankid=rid, acctid=to_account, accttype=to_type.upper())

    intraxferrq = INTRAXFERRQ(
        xferinfo=XFERINFO(
            bankacctfrom=bankacctfrom,
            bankacctto=bankacctto,
            trnamt=amount,
        ),
    )

    intratrnrq = INTRATRNRQ(trnuid=_generate_trnuid(), intraxferrq=intraxferrq)
    signonmsgs = SIGNONMSGSRQV1(sonrq=signon)
    bankmsgs = BANKMSGSRQV1(intratrnrq)
    ofx = OFX(signonmsgsrqv1=signonmsgs, bankmsgsrqv1=bankmsgs)

    ofx_str = str(make_header(version=profile["ofx_version"])) + ET.tostring(ofx.to_etree(), encoding="unicode")
    raw_resp = _post_ofx(profile, ofx_str)

    parser = OFXTree()
    parser.parse(BytesIO(raw_resp))
    ofx_resp = parser.convert()

    result = {"status": "unknown", "srvrtid": "", "message": ""}
    if hasattr(ofx_resp, "bankmsgsrsv1") and ofx_resp.bankmsgsrsv1:
        for trnrs in ofx_resp.bankmsgsrsv1:
            status = getattr(trnrs, "status", None)
            if status:
                result["status"] = "success" if status.code == 0 else f"error({status.code})"
                result["message"] = getattr(status, "message", "")
            result["srvrtid"] = getattr(trnrs, "srvrtid", "")
    return result


def send_interbank_transfer(bank_key: str, username: str, password: str,
                            from_account: str, from_routing: str,
                            to_account: str, to_routing: str, amount: float,
                            memo: str = "",
                            from_type: str = "CHECKING", to_type: str = "CHECKING") -> dict:
    """Execute an interbank (ACH) funds transfer via OFX INTERBANKTRNRQ."""
    profile = BANK_PROFILES[bank_key]
    signon = _build_signon(profile, username, password)

    bankacctfrom = BANKACCTFROM(bankid=from_routing, acctid=from_account, accttype=from_type.upper())
    bankacctto = BANKACCTTO(bankid=to_routing, acctid=to_account, accttype=to_type.upper())

    interrq = INTERRQ(
        xferinfo=XFERINFO(
            bankacctfrom=bankacctfrom,
            bankacctto=bankacctto,
            trnamt=amount,
        ),
    )

    intertrnrq = INTERTRNRQ(trnuid=_generate_trnuid(), interrq=interrq)
    signonmsgs = SIGNONMSGSRQV1(sonrq=signon)
    intermsgs = INTERXFERMSGSRQV1(intertrnrq)
    ofx = OFX(signonmsgsrqv1=signonmsgs, interxfermsgsrqv1=intermsgs)

    ofx_str = str(make_header(version=profile["ofx_version"])) + ET.tostring(ofx.to_etree(), encoding="unicode")
    raw_resp = _post_ofx(profile, ofx_str)

    # Write raw response for inspection
    with open("debug_response.xml", "wb") as f:
        f.write(raw_resp)

    parser = OFXTree()
    parser.parse(BytesIO(raw_resp))
    ofx_resp = parser.convert()

    # Log which message-set attributes came back
    resp_attrs = [a for a in dir(ofx_resp) if not a.startswith("_") and getattr(ofx_resp, a, None)]
    console.print(f"[dim]Response attrs: {resp_attrs}[/dim]")

    result = {"status": "unknown", "srvrtid": "", "message": ""}

    # Primary: interbank transfer response
    if hasattr(ofx_resp, "interxfermsgsrsv1") and ofx_resp.interxfermsgsrsv1:
        for trnrs in ofx_resp.interxfermsgsrsv1:
            status = getattr(trnrs, "status", None)
            if status:
                result["status"] = "success" if status.code == 0 else f"error({status.code})"
                result["message"] = getattr(status, "message", "")
            result["srvrtid"] = getattr(trnrs, "srvrtid", "")

    # Fallback: some servers return INTERXFER result inside bankmsgsrsv1
    elif hasattr(ofx_resp, "bankmsgsrsv1") and ofx_resp.bankmsgsrsv1:
        for trnrs in ofx_resp.bankmsgsrsv1:
            status = getattr(trnrs, "status", None)
            if status:
                result["status"] = "success" if status.code == 0 else f"error({status.code})"
                result["message"] = getattr(status, "message", "")
            result["srvrtid"] = getattr(trnrs, "srvrtid", "")

    # Fallback: signon-level error
    elif hasattr(ofx_resp, "signonmsgsrsv1") and ofx_resp.signonmsgsrsv1:
        sonrs = ofx_resp.signonmsgsrsv1.sonrs
        if sonrs.status.code != 0:
            result["status"] = f"error({sonrs.status.code})"
            result["message"] = getattr(sonrs.status, "message", "Signon failed")

    return result


def send_billpay_transfer(bank_key: str, username: str, password: str,
                          from_account: str, from_routing: str,
                          payee_name: str, payee_addr1: str, payee_city: str,
                          payee_state: str, payee_zip: str, payee_phone: str,
                          payee_account: str, amount: float,
                          due_date: datetime.datetime = None,
                          memo: str = "",
                          from_type: str = "CHECKING") -> dict:
    """Send a bill payment via OFX BILLPAYMSGSRQV1 / PMTTRNRQ."""
    profile = BANK_PROFILES[bank_key]
    if not profile.get("supports_billpay"):
        return {"status": "error", "message": f"{profile['name']} does not support OFX bill pay."}

    signon = _build_signon(profile, username, password)

    if due_date is None:
        due_date = _now_dt() + datetime.timedelta(days=3)

    bankacctfrom = BANKACCTFROM(
        bankid=from_routing,
        acctid=from_account,
        accttype=from_type.upper(),
    )

    payee = PAYEE(
        name=payee_name[:32],
        addr1=payee_addr1[:32],
        city=payee_city[:32],
        state=payee_state[:5],
        postalcode=payee_zip[:11],
        phone=payee_phone[:32],
    )

    pmtinfo = PMTINFO(
        bankacctfrom=bankacctfrom,
        trnamt=amount,
        payee=payee,
        payacct=payee_account[:32],
        dtdue=due_date,
        memo=memo or None,
    )

    pmttrnrq = PMTTRNRQ(
        trnuid=_generate_trnuid(),
        pmtrq=PMTRQ(pmtinfo=pmtinfo),
    )

    signonmsgs = SIGNONMSGSRQV1(sonrq=signon)
    billpaymsgs = BILLPAYMSGSRQV1(pmttrnrq)
    ofx = OFX(signonmsgsrqv1=signonmsgs, billpaymsgsrqv1=billpaymsgs)

    ofx_str = str(make_header(version=profile["ofx_version"])) + ET.tostring(ofx.to_etree(), encoding="unicode")
    raw_resp = _post_ofx(profile, ofx_str)

    with open("debug_response.xml", "wb") as f:
        f.write(raw_resp)

    parser = OFXTree()
    parser.parse(BytesIO(raw_resp))
    ofx_resp = parser.convert()

    resp_attrs = [a for a in dir(ofx_resp) if not a.startswith("_") and getattr(ofx_resp, a, None)]
    console.print(f"[dim]Response attrs: {resp_attrs}[/dim]")

    result = {"status": "unknown", "srvrtid": "", "message": ""}

    if hasattr(ofx_resp, "billpaymsgsrsv1") and ofx_resp.billpaymsgsrsv1:
        for trnrs in ofx_resp.billpaymsgsrsv1:
            status = getattr(trnrs, "status", None)
            if status:
                result["status"] = "success" if status.code == 0 else f"error({status.code})"
                result["message"] = getattr(status, "message", "")
            result["srvrtid"] = getattr(trnrs, "srvrtid", "")
    elif hasattr(ofx_resp, "signonmsgsrsv1") and ofx_resp.signonmsgsrsv1:
        sonrs = ofx_resp.signonmsgsrsv1.sonrs
        if sonrs.status.code != 0:
            result["status"] = f"error({sonrs.status.code})"
            result["message"] = getattr(sonrs.status, "message", "Signon failed")

    return result


def send_wire_transfer(bank_key: str, username: str, password: str,
                       from_account: str, from_routing: str,
                       to_account: str, to_routing: str, amount: float,
                       payee_name: str, memo: str = "",
                       from_type: str = "CHECKING") -> dict:
    """Execute a wire transfer via OFX WIRETRNRQ."""
    profile = BANK_PROFILES[bank_key]
    if not profile.get("supports_wire"):
        return {"status": "error", "message": f"{profile['name']} does not support wire transfers via OFX."}

    signon = _build_signon(profile, username, password)

    bankacctfrom = BANKACCTFROM(bankid=from_routing, acctid=from_account, accttype=from_type.upper())

    extbankdesc = EXTBANKDESC(
        name="Destination Bank",
        bankid=to_routing,
        addr1="123 Bank St",
        city="City",
        state="ST",
        postalcode="12345"
    )
    wiredestbank = WIREDESTBANK(extbankdesc=extbankdesc)
    wirebeneficiary = WIREBENEFICIARY(name=payee_name, bankacctto=BANKACCTTO(
        bankid=to_routing, acctid=to_account, accttype="CHECKING"
    ))

    wirerq = WIRERQ(
        bankacctfrom=bankacctfrom,
        wiredestbank=wiredestbank,
        wirebeneficiary=wirebeneficiary,
        trnamt=amount,
    )

    wiretrnrq = WIRETRNRQ(trnuid=_generate_trnuid(), wirerq=wirerq)
    signonmsgs = SIGNONMSGSRQV1(sonrq=signon)
    bankmsgs = BANKMSGSRQV1(wiretrnrq)
    ofx = OFX(signonmsgsrqv1=signonmsgs, bankmsgsrqv1=bankmsgs)

    ofx_str = str(make_header(version=profile["ofx_version"])) + ET.tostring(ofx.to_etree(), encoding="unicode")
    raw_resp = _post_ofx(profile, ofx_str)

    parser = OFXTree()
    parser.parse(BytesIO(raw_resp))
    ofx_resp = parser.convert()

    result = {"status": "unknown", "srvrtid": "", "message": ""}
    if hasattr(ofx_resp, "bankmsgsrsv1") and ofx_resp.bankmsgsrsv1:
        for trnrs in ofx_resp.bankmsgsrsv1:
            status = getattr(trnrs, "status", None)
            if status:
                result["status"] = "success" if status.code == 0 else f"error({status.code})"
                result["message"] = getattr(status, "message", "")
            result["srvrtid"] = getattr(trnrs, "srvrtid", "")
    return result


def display_balance(balance_info: dict):
    """Pretty-print account balance."""
    table = Table(title="Account Balance")
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Account ID", balance_info["account_id"])
    table.add_row("Available Balance", f"${balance_info['available']:,.2f}")
    table.add_row("Ledger Balance", f"${balance_info['ledger']:,.2f}")
    console.print(table)


def display_transfer_result(result: dict, transfer_type: str = "Transfer"):
    """Pretty-print transfer result."""
    color = "green" if result["status"] == "success" else "red"
    console.print(f"\n[bold {color}]{transfer_type} Result: {result['status']}[/bold {color}]")
    if result.get("srvrtid"):
        console.print(f"  Server Transaction ID: {result['srvrtid']}")
    if result.get("message"):
        console.print(f"  Message: {result['message']}")


def get_history(bank_key: str, username: str, password: str, account_id: str,
                days: int = 30, routing_number: str = "", acct_type: str = "CHECKING") -> list[dict]:
    """Fetch transaction history via OFX statement request."""
    profile = BANK_PROFILES[bank_key]

    signon = _build_signon(profile, username, password)

    bankacctfrom = BANKACCTFROM(
        bankid=routing_number or profile.get("fid", ""),
        acctid=account_id,
        accttype=acct_type.upper(),
    )

    # Calculate start date
    start_date = datetime.datetime.now(UTC) - datetime.timedelta(days=days)

    stmtrq = STMTRQ(
        bankacctfrom=bankacctfrom,
        inctran=INCTRAN(dtstart=start_date, include=True),
    )

    stmttrnrq = STMTTRNRQ(trnuid=_generate_trnuid(), stmtrq=stmtrq)
    signonmsgs = SIGNONMSGSRQV1(sonrq=signon)
    bankmsgs = BANKMSGSRQV1(stmttrnrq)
    ofx = OFX(signonmsgsrqv1=signonmsgs, bankmsgsrqv1=bankmsgs)

    ofx_str = str(make_header(version=profile["ofx_version"])) + ET.tostring(ofx.to_etree(), encoding="unicode")
    raw_resp = _post_ofx(profile, ofx_str)

    # Write raw response to debug file
    with open("debug_response.xml", "wb") as f:
        f.write(raw_resp)

    parser = OFXTree()
    parser.parse(BytesIO(raw_resp))
    ofx_resp = parser.convert()

    # Check Signon status
    if hasattr(ofx_resp, "signonmsgsrsv1") and ofx_resp.signonmsgsrsv1:
        sonrs = ofx_resp.signonmsgsrsv1.sonrs
        if sonrs.status.code != 0:
            raise Exception(f"Signon failed (Code: {sonrs.status.code}, Message: {sonrs.status.message})")

    transactions = []
    if hasattr(ofx_resp, "bankmsgsrsv1") and ofx_resp.bankmsgsrsv1:
        for trnrs in ofx_resp.bankmsgsrsv1:
            if trnrs.status.code != 0:
                raise Exception(f"Statement transaction failed (Code: {trnrs.status.code}, Message: {trnrs.status.message})")
            if hasattr(trnrs, "stmtrs") and trnrs.stmtrs:
                stmtrs = trnrs.stmtrs
                if hasattr(stmtrs, "banktranlist") and stmtrs.banktranlist:
                    # Iterate transactions
                    for stmttrn in getattr(stmtrs.banktranlist, "stmttrn", []):
                        transactions.append({
                            "type": getattr(stmttrn, "trntype", "UNKNOWN"),
                            "date": getattr(stmttrn, "dtposted", None),
                            "amount": float(getattr(stmttrn, "trnamt", 0.0)),
                            "fitid": getattr(stmttrn, "fitid", "N/A"),
                            "name": getattr(stmttrn, "name", "N/A"),
                            "memo": getattr(stmttrn, "memo", "N/A"),
                        })
    return transactions


def display_history(transactions: list[dict]):
    """Pretty-print transaction list."""
    from rich import box
    if not transactions:
        console.print("[yellow]No transactions found for the specified period.[/yellow]")
        return

    table = Table(title="Transaction History", box=box.ASCII)
    table.add_column("Date", style="cyan")
    table.add_column("Type", style="white")
    table.add_column("Amount", style="green")
    table.add_column("Name / Payee", style="white")
    table.add_column("Memo", style="dim white")
    table.add_column("FITID", style="dim cyan")

    for tx in sorted(transactions, key=lambda x: x["date"] or datetime.datetime.min, reverse=True):
        date_str = tx["date"].strftime("%Y-%m-%d") if tx["date"] else "N/A"
        amt_color = "green" if tx["amount"] >= 0 else "red"
        amt_str = f"[{amt_color}]${tx['amount']:+,.2f}[/{amt_color}]"
        table.add_row(
            date_str,
            tx["type"],
            amt_str,
            tx["name"],
            tx["memo"],
            tx["fitid"]
        )
    console.print(table)


def parse_local_file(file_path: str) -> list[dict]:
    """Parse local OFX/QFX statement file and extract transactions."""
    parser = OFXTree()
    parser.parse(file_path)
    ofx_resp = parser.convert()

    transactions = []
    # Try parsing bank statements
    if hasattr(ofx_resp, "bankmsgsrsv1") and ofx_resp.bankmsgsrsv1:
        for trnrs in ofx_resp.bankmsgsrsv1:
            if hasattr(trnrs, "stmtrs") and trnrs.stmtrs:
                stmtrs = trnrs.stmtrs
                if hasattr(stmtrs, "banktranlist") and stmtrs.banktranlist:
                    for stmttrn in getattr(stmtrs.banktranlist, "stmttrn", []):
                        transactions.append({
                            "type": getattr(stmttrn, "trntype", "UNKNOWN"),
                            "date": getattr(stmttrn, "dtposted", None),
                            "amount": float(getattr(stmttrn, "trnamt", 0.0)),
                            "fitid": getattr(stmttrn, "fitid", "N/A"),
                            "name": getattr(stmttrn, "name", "N/A"),
                            "memo": getattr(stmttrn, "memo", "N/A"),
                        })
    # Try parsing credit card statements
    if hasattr(ofx_resp, "ccmsgsrsv1") and ofx_resp.ccmsgsrsv1:
        for trnrs in ofx_resp.ccmsgsrsv1:
            if hasattr(trnrs, "ccstmtrs") and trnrs.ccstmtrs:
                stmtrs = trnrs.ccstmtrs
                if hasattr(stmtrs, "banktranlist") and stmtrs.banktranlist:
                    for stmttrn in getattr(stmtrs.banktranlist, "stmttrn", []):
                        transactions.append({
                            "type": getattr(stmttrn, "trntype", "UNKNOWN"),
                            "date": getattr(stmttrn, "dtposted", None),
                            "amount": float(getattr(stmttrn, "trnamt", 0.0)),
                            "fitid": getattr(stmttrn, "fitid", "N/A"),
                            "name": getattr(stmttrn, "name", "N/A"),
                            "memo": getattr(stmttrn, "memo", "N/A"),
                        })
    return transactions
