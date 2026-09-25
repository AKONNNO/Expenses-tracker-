#!/usr/bin/env python3
import os
os.environ.setdefault("PYTHONUTF8", "1")
"""
bank_auto — CLI tool for personal banking automation via OFX.
Supports PNC, Huntington, SecurTrust.

Usage:
    python main.py setup <bank>         — store credentials for a bank
    python main.py balance <bank>       — check account balance
    python main.py transfer             — interactive transfer wizard
    python main.py send <bank> ...      — quick one-liner transfer
    python main.py accounts <bank>      — list available accounts
    python main.py remove <bank>        — remove stored credentials
"""

import sys
import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich import box

from config import BANK_PROFILES
from cred_manager import (
    prompt_and_store,
    get_credentials,
    delete_credentials,
    get_payees,
    store_payee,
    delete_payee,
)
from ofx_client import (
    get_accounts,
    get_balance,
    send_intrabank_transfer,
    send_interbank_transfer,
    send_billpay_transfer,
    send_wire_transfer,
    display_balance,
    display_transfer_result,
    get_history,
    display_history,
    parse_local_file,
)

console = Console(force_terminal=True)

BANNER = r"""
[bold cyan]
 ____    _    _   _ _  __     _   _   _ _____ ___
| __ )  / \  | \ | | |/ /    / \ | | | |_   _/ _ \\
|  _ \ / _ \ |  \| | ' /    / _ \| | | | | || | | |
| |_) / ___ \| |\  | . \   / ___ \ |_| | | || |_| |
|____/_/   \_\_| \_|_|\_\ /_/   \_\___/  |_| \___/
[/bold cyan]
[dim]Personal Banking Automation -- OFX Protocol[/dim]
"""

BANK_CHOICES = list(BANK_PROFILES.keys())


def _validate_bank(bank_key: str) -> bool:
    if bank_key not in BANK_PROFILES:
        console.print(f"[red]Unknown bank: {bank_key}[/red]")
        console.print(f"[dim]Supported banks: {', '.join(BANK_CHOICES)}[/dim]")
        return False
    return True


def _get_creds_or_fail(bank_key: str) -> dict | None:
    creds = get_credentials(bank_key)
    if not creds:
        console.print(f"[red]No credentials stored for {BANK_PROFILES[bank_key]['name']}.[/red]")
        console.print(f"[dim]Run: python main.py setup {bank_key}[/dim]")
        return None
    return creds


@click.group()
def cli():
    """Personal banking automation via OFX protocol."""
    pass


@cli.command()
@click.argument("bank", type=click.Choice(BANK_CHOICES, case_sensitive=False))
def setup(bank: str):
    """Store credentials for a bank in the OS keyring."""
    console.print(BANNER)
    bank_lower = bank.lower()
    prompt_and_store(bank_lower)
    if bank_lower == "pnc":
        console.print("\n[bold yellow]PNC Bank requires browser-based automation setup to bypass MFA/2FA.[/bold yellow]")
        if Confirm.ask("[bold yellow]Launch browser setup now to log in and save session cookies?[/bold yellow]", default=True):
            try:
                import pnc_browser
                pnc_browser.cmd_setup()
            except Exception as e:
                console.print(f"[red]Error running browser setup: {e}[/red]")
                console.print("[yellow]You can run it manually later with: python pnc_browser.py setup[/yellow]")


@cli.command()
@click.argument("bank", type=click.Choice(BANK_CHOICES, case_sensitive=False))
def remove(bank: str):
    """Remove stored credentials for a bank."""
    delete_credentials(bank.lower())


@cli.command()
@click.argument("bank", type=click.Choice(BANK_CHOICES, case_sensitive=False))
def accounts(bank: str):
    """List available accounts at a bank."""
    bank = bank.lower()
    creds = _get_creds_or_fail(bank)
    if not creds:
        return

    if not creds.get("username") or not creds.get("password"):
        console.print(f"\n[yellow]Note: {BANK_PROFILES[bank]['name']} is configured as ACH-only.[/yellow]")
        console.print("[red]Account listing via the OFX server is not supported without online banking credentials.[/red]")
        console.print("[dim]Your configured account IDs for this bank:[/dim]")
        for aid in creds.get("account_ids", []):
            console.print(f"  • {aid}")
        return

    console.print(f"\n[bold]Querying accounts at {BANK_PROFILES[bank]['name']}...[/bold]")
    try:
        accts = get_accounts(bank, creds["username"], creds["password"])
        if not accts:
            console.print("[yellow]No accounts returned. Your stored account IDs:[/yellow]")
            for aid in creds.get("account_ids", []):
                console.print(f"  • {aid}")
            return

        table = Table(title=f"{BANK_PROFILES[bank]['name']} Accounts", box=box.ROUNDED)
        table.add_column("Account ID", style="cyan")
        table.add_column("Type", style="green")
        table.add_column("Name", style="white")
        for a in accts:
            table.add_row(a.get("acctid", "N/A"), a.get("type", "N/A"), a.get("name", "N/A"))
        console.print(table)
    except Exception as e:
        console.print(f"[red]Error querying accounts: {e}[/red]")


@cli.command()
@click.argument("bank", type=click.Choice(BANK_CHOICES, case_sensitive=False))
@click.option("--account", "-a", default=None, help="Specific account ID (default: first stored)")
@click.option("--routing", "-r", default="", help="Routing number override")
@click.option("--type", "acct_type", default="CHECKING", help="Account type: CHECKING, SAVINGS, etc.")
def balance(bank: str, account: str, routing: str, acct_type: str):
    """Check account balance."""
    bank = bank.lower()
    creds = _get_creds_or_fail(bank)
    if not creds:
        return

    if creds.get("is_ach_only") or not creds.get("username") or not creds.get("password"):
        console.print(f"\n[yellow]Note: {BANK_PROFILES[bank]['name']} is configured as ACH-only (no login credentials).[/yellow]")
        console.print("[red]Real-time balance queries via the OFX server are not supported without online banking credentials.[/red]")
        console.print("[dim]You can still execute real-time ACH Pulls/Pushes to/from this bank using another configured bank.[/dim]")
        return

    acct_id = account or creds["account_ids"][0]
    console.print(f"\n[bold]Fetching balance for account {acct_id}...[/bold]")

    try:
        result = get_balance(bank, creds["username"], creds["password"], acct_id, routing, acct_type)
        display_balance(result)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


@cli.command()
@click.argument("bank", type=click.Choice(BANK_CHOICES, case_sensitive=False))
@click.option("--from-account", "-f", required=True, help="Source account ID")
@click.option("--to-account", "-t", required=True, help="Destination account ID")
@click.option("--amount", "-a", required=True, type=float, help="Transfer amount in USD")
@click.option("--memo", "-m", default="", help="Transfer memo")
@click.option("--routing", "-r", default="", help="Routing number (for intrabank)")
@click.option("--from-type", default="CHECKING", help="Source account type")
@click.option("--to-type", default="CHECKING", help="Destination account type")
def send_internal(bank: str, from_account: str, to_account: str, amount: float,
                  memo: str, routing: str, from_type: str, to_type: str):
    """Send money between your own accounts at the same bank."""
    bank = bank.lower()
    creds = _get_creds_or_fail(bank)
    if not creds:
        return

    console.print(Panel(
        f"[bold]Intrabank Transfer[/bold]\n"
        f"Bank: {BANK_PROFILES[bank]['name']}\n"
        f"From: {from_account} ({from_type})\n"
        f"To:   {to_account} ({to_type})\n"
        f"Amount: [green]${amount:,.2f}[/green]",
        title="Confirm Transfer",
        border_style="yellow",
    ))

    if not Confirm.ask("[bold yellow]Execute this transfer?[/bold yellow]"):
        console.print("[dim]Transfer cancelled.[/dim]")
        return

    try:
        if bank == "pnc":
            import pnc_browser
            result = pnc_browser.transfer_internal(
                creds["username"], creds["password"],
                from_account, to_account, amount, memo
            )
            display_transfer_result(result, "Intrabank Transfer (Browser)")
        else:
            result = send_intrabank_transfer(
                bank, creds["username"], creds["password"],
                from_account, to_account, amount, memo, routing, from_type, to_type,
            )
            display_transfer_result(result, "Intrabank Transfer")
    except Exception as e:
        console.print(f"[red]Transfer failed: {e}[/red]")


@cli.command()
@click.argument("bank", type=click.Choice(BANK_CHOICES, case_sensitive=False))
@click.option("--from-account", "-f", required=True, help="Source account ID")
@click.option("--from-routing", required=True, help="Source routing number")
@click.option("--to-account", "-t", required=True, help="Destination account ID")
@click.option("--to-routing", required=True, help="Destination routing number")
@click.option("--amount", "-a", required=True, type=float, help="Transfer amount in USD")
@click.option("--memo", "-m", default="", help="Transfer memo")
@click.option("--from-type", default="CHECKING", help="Source account type")
@click.option("--to-type", default="CHECKING", help="Destination account type")
@click.option("--pull", is_flag=True, help="Execute as an ACH Pull (debit) from the destination account")
def send_ach(bank: str, from_account: str, from_routing: str,
             to_account: str, to_routing: str, amount: float,
             memo: str, from_type: str, to_type: str, pull: bool):
    """Send money to another bank via ACH (interbank transfer)."""
    bank = bank.lower()
    creds = _get_creds_or_fail(bank)
    if not creds:
        return

    dir_title = "ACH Pull (Debit)" if pull else "ACH Push (Credit)"
    console.print(Panel(
        f"[bold]{dir_title} Transfer[/bold]\n"
        f"From (Debited): {to_account if pull else from_account} @ routing {to_routing if pull else from_routing} ({to_type if pull else from_type})\n"
        f"To (Credited):   {from_account if pull else to_account} @ routing {from_routing if pull else to_routing} ({from_type if pull else to_type})\n"
        f"Amount: [green]${amount:,.2f}[/green]\n"
        f"[dim]Settlement: 1-3 business days[/dim]",
        title=f"Confirm {dir_title} Transfer",
        border_style="yellow",
    ))

    if not Confirm.ask(f"[bold yellow]Execute this {dir_title} transfer?[/bold yellow]"):
        console.print("[dim]Transfer cancelled.[/dim]")
        return

    # If pull, swap the accounts in the OFX call so the external payee is 'from' and user account is 'to'
    actual_from_acct = to_account if pull else from_account
    actual_from_rout = to_routing if pull else from_routing
    actual_from_type = to_type if pull else from_type

    actual_to_acct = from_account if pull else to_account
    actual_to_rout = from_routing if pull else to_routing
    actual_to_type = from_type if pull else to_type

    try:
        if bank == "pnc":
            if pull:
                console.print("[red]ACH Pull (Debit) is not supported for PNC via browser automation.[/red]")
                return
            to_name = ""
            for p in get_payees().values():
                if p["account"] == to_account and p["routing"] == to_routing:
                    to_name = p["name"]
                    break
            if not to_name:
                to_name = Prompt.ask("Enter recipient name (must match name registered in PNC external accounts)", default="External Recipient")
            
            import pnc_browser
            result = pnc_browser.transfer_external(
                creds["username"], creds["password"],
                from_account, to_routing, to_account, to_name, amount, memo
            )
            display_transfer_result(result, "ACH Push (Browser)")
        else:
            result = send_interbank_transfer(
                bank, creds["username"], creds["password"],
                actual_from_acct, actual_from_rout, actual_to_acct, actual_to_rout, amount, memo, actual_from_type, actual_to_type,
            )
            display_transfer_result(result, dir_title)
    except Exception as e:
        console.print(f"[red]Transfer failed: {e}[/red]")


@cli.command()
@click.argument("bank", type=click.Choice(BANK_CHOICES, case_sensitive=False))
@click.option("--from-account", "-f", required=True, help="Source account ID")
@click.option("--from-routing", required=True, help="Source routing number")
@click.option("--payee-name", "-p", required=True, help="Payee / recipient name")
@click.option("--payee-addr", required=True, help="Payee street address")
@click.option("--payee-city", required=True, help="Payee city")
@click.option("--payee-state", required=True, help="Payee state (2-letter)")
@click.option("--payee-zip", required=True, help="Payee ZIP code")
@click.option("--payee-phone", required=True, help="Payee phone number")
@click.option("--payee-account", required=True, help="Payee account / reference number")
@click.option("--amount", "-a", required=True, type=float, help="Payment amount in USD")
@click.option("--memo", "-m", default="", help="Payment memo")
@click.option("--from-type", default="CHECKING", help="Source account type")
def send_billpay(bank: str, from_account: str, from_routing: str,
                 payee_name: str, payee_addr: str, payee_city: str,
                 payee_state: str, payee_zip: str, payee_phone: str,
                 payee_account: str, amount: float, memo: str, from_type: str):
    """Send a bill payment via OFX BillPay."""
    bank = bank.lower()
    creds = _get_creds_or_fail(bank)
    if not creds:
        return

    console.print(Panel(
        f"[bold]Bill Payment[/bold]\n"
        f"From: {from_account} @ {from_routing} ({from_type})\n"
        f"To:   {payee_name} — acct {payee_account}\n"
        f"Amount: [green]${amount:,.2f}[/green]",
        title="Confirm Bill Payment",
        border_style="yellow",
    ))

    if not Confirm.ask("[bold yellow]Execute this payment?[/bold yellow]"):
        console.print("[dim]Payment cancelled.[/dim]")
        return

    try:
        if bank == "pnc":
            import pnc_browser
            result = pnc_browser.send_billpay(
                creds["username"], creds["password"],
                from_account, payee_name, payee_account, amount, memo
            )
            display_transfer_result(result, "Bill Payment (Browser)")
        else:
            result = send_billpay_transfer(
                bank, creds["username"], creds["password"],
                from_account, from_routing,
                payee_name, payee_addr, payee_city, payee_state, payee_zip, payee_phone,
                payee_account, amount, None, memo, from_type,
            )
            display_transfer_result(result, "Bill Payment")
    except Exception as e:
        console.print(f"[red]Payment failed: {e}[/red]")


@cli.command()
@click.argument("bank", type=click.Choice(BANK_CHOICES, case_sensitive=False))
@click.option("--from-account", "-f", required=True, help="Source account ID")
@click.option("--from-routing", required=True, help="Source routing number")
@click.option("--to-account", "-t", required=True, help="Destination account ID")
@click.option("--to-routing", required=True, help="Destination routing number")
@click.option("--amount", "-a", required=True, type=float, help="Transfer amount in USD")
@click.option("--payee", "-p", required=True, help="Recipient name")
@click.option("--memo", "-m", default="", help="Wire memo")
@click.option("--from-type", default="CHECKING", help="Source account type")
def send_wire(bank: str, from_account: str, from_routing: str,
              to_account: str, to_routing: str, amount: float,
              payee: str, memo: str, from_type: str):
    """Send a wire transfer (same-day, higher fees)."""
    bank = bank.lower()
    creds = _get_creds_or_fail(bank)
    if not creds:
        return

    profile = BANK_PROFILES[bank]
    if not profile.get("supports_wire"):
        console.print(f"[red]{profile['name']} does not support wire transfers via OFX.[/red]")
        return

    console.print(Panel(
        f"[bold]Wire Transfer[/bold]\n"
        f"From: {from_account} @ routing {from_routing} ({from_type})\n"
        f"To:   {payee}\n"
        f"      {to_account} @ routing {to_routing}\n"
        f"Amount: [green]${amount:,.2f}[/green]\n"
        f"[bold red]⚠ Wire transfers are immediate and typically non-reversible[/bold red]",
        title="Confirm Wire Transfer",
        border_style="red",
    ))

    if not Confirm.ask("[bold red]Execute this wire transfer?[/bold red]"):
        console.print("[dim]Transfer cancelled.[/dim]")
        return

    try:
        result = send_wire_transfer(
            bank, creds["username"], creds["password"],
            from_account, from_routing, to_account, to_routing, amount, payee, memo, from_type,
        )
        display_transfer_result(result, "Wire Transfer")
    except Exception as e:
        console.print(f"[red]Transfer failed: {e}[/red]")


@cli.command()
def transfer():
    """Interactive transfer wizard — walks you through the process."""
    console.print(BANNER)
    console.print("[bold]Transfer Wizard[/bold]\n")

    # Select source bank
    console.print("[cyan]Available banks:[/cyan]")
    for i, key in enumerate(BANK_CHOICES, 1):
        name = BANK_PROFILES[key]["name"]
        creds = get_credentials(key)
        status = "[green]configured[/green]" if creds else "[red]not configured[/red]"
        console.print(f"  {i}. {name} — {status}")

    bank_idx = int(Prompt.ask("\nSelect source bank", choices=[str(i) for i in range(1, len(BANK_CHOICES) + 1)])) - 1
    src_bank = BANK_CHOICES[bank_idx]
    creds = _get_creds_or_fail(src_bank)
    if not creds:
        return

    if creds.get("is_ach_only") or not creds.get("username") or not creds.get("password"):
        console.print(f"\n[yellow]{BANK_PROFILES[src_bank]['name']} is configured as ACH-only (no login credentials).[/yellow]")
        console.print("[cyan]To transfer funds, we must execute the transfer through another bank with credentials.[/cyan]\n")

        # List configured banks
        configured_banks = []
        for key in BANK_CHOICES:
            c = get_credentials(key)
            if c and not c.get("is_ach_only"):
                configured_banks.append(key)

        if not configured_banks:
            console.print("[red]No other banks with credentials are configured. Please run 'python main.py setup <bank>' on another bank first.[/red]")
            return

        console.print("[cyan]Select logged-in bank to execute transfer:[/cyan]")
        for i, key in enumerate(configured_banks, 1):
            console.print(f"  {i}. {BANK_PROFILES[key]['name']}")
        login_bank_idx = int(Prompt.ask("Select bank", choices=[str(i) for i in range(1, len(configured_banks) + 1)])) - 1
        login_bank = configured_banks[login_bank_idx]
        login_creds = get_credentials(login_bank)

        # Now we ask: Pull from ACH-only to login_bank, or Push from login_bank to ACH-only?
        console.print(f"\n[cyan]Select direction relative to {BANK_PROFILES[src_bank]['name']}:[/cyan]")
        console.print(f"  1. Pull (Request/pull money from {BANK_PROFILES[src_bank]['name']} into {BANK_PROFILES[login_bank]['name']})")
        console.print(f"  2. Push (Send money from {BANK_PROFILES[login_bank]['name']} to {BANK_PROFILES[src_bank]['name']})")
        dir_choice = Prompt.ask("Direction", choices=["1", "2"])

        # Select SecurTrust account
        console.print(f"\n[cyan]Select {BANK_PROFILES[src_bank]['name']} account:[/cyan]")
        for i, aid in enumerate(creds["account_ids"], 1):
            console.print(f"  {i}. {aid}")
        acct_idx = int(Prompt.ask("Select account", choices=[str(i) for i in range(1, len(creds["account_ids"]) + 1)])) - 1
        ach_account = creds["account_ids"][acct_idx]
        ach_routing = creds.get("routing", "")
        if not ach_routing:
            ach_routing = Prompt.ask(f"Enter routing number for {BANK_PROFILES[src_bank]['name']}")
            # Save it back to the keyring
            creds["routing"] = ach_routing
            import keyring
            import json
            from config import KEYRING_SERVICE
            keyring.set_password(KEYRING_SERVICE, src_bank, json.dumps(creds))
        ach_type = Prompt.ask("Account type", choices=["CHECKING", "SAVINGS", "MONEYMRKT", "CREDITLINE", "CD"], default="CHECKING").upper()

        # Select login bank account
        console.print(f"\n[cyan]Select {BANK_PROFILES[login_bank]['name']} account:[/cyan]")
        for i, aid in enumerate(login_creds["account_ids"], 1):
            console.print(f"  {i}. {aid}")
        acct_idx = int(Prompt.ask("Select account", choices=[str(i) for i in range(1, len(login_creds["account_ids"]) + 1)])) - 1
        login_account = login_creds["account_ids"][acct_idx]
        login_routing = Prompt.ask(f"Your {BANK_PROFILES[login_bank]['name']} routing number")
        login_type = Prompt.ask("Account type", choices=["CHECKING", "SAVINGS", "MONEYMRKT", "CREDITLINE", "CD"], default="CHECKING").upper()

        amount = float(Prompt.ask("\nAmount (USD)"))
        memo = Prompt.ask("Memo (optional)", default="")

        is_pull = (dir_choice == "1") # Pull from ACH-only to login bank
        dir_title = "ACH Pull (Debit)" if is_pull else "ACH Push (Credit)"

        console.print(Panel(
            f"[bold]{dir_title} Transfer[/bold]\n"
            f"From (Debited): {ach_account if is_pull else login_account} @ {ach_routing if is_pull else login_routing}\n"
            f"To (Credited):   {login_account if is_pull else ach_account} @ {login_routing if is_pull else ach_routing}\n"
            f"Amount: [green]${amount:,.2f}[/green]",
            title="Confirm", border_style="yellow",
        ))

        if Confirm.ask("[bold yellow]Execute?[/bold yellow]"):
            actual_from_acct = ach_account if is_pull else login_account
            actual_from_rout = ach_routing if is_pull else login_routing
            actual_from_type = ach_type if is_pull else login_type

            actual_to_acct = login_account if is_pull else ach_account
            actual_to_rout = login_routing if is_pull else ach_routing
            actual_to_type = login_type if is_pull else ach_type

            try:
                if login_bank == "pnc":
                    if is_pull:
                        console.print("[red]ACH Pull (Debit) is not supported for PNC via browser automation.[/red]")
                        return
                    to_name = ""
                    for p in get_payees().values():
                        if p["account"] == actual_to_acct and p["routing"] == actual_to_rout:
                            to_name = p["name"]
                            break
                    if not to_name:
                        to_name = Prompt.ask("Enter recipient name (must match name registered in PNC external accounts)", default="External Recipient")
                    
                    import pnc_browser
                    result = pnc_browser.transfer_external(
                        login_creds["username"], login_creds["password"],
                        actual_from_acct, actual_to_rout, actual_to_acct, to_name, amount, memo
                    )
                    display_transfer_result(result, "ACH Push (Browser)")
                else:
                    result = send_interbank_transfer(
                        login_bank, login_creds["username"], login_creds["password"],
                        actual_from_acct, actual_from_rout,
                        actual_to_acct, actual_to_rout,
                        amount, memo, actual_from_type, actual_to_type,
                    )
                    display_transfer_result(result, dir_title)
            except Exception as e:
                console.print(f"[red]Transfer failed: {e}[/red]")
        return

    # Select source account
    console.print(f"\n[cyan]Your {BANK_PROFILES[src_bank]['name']} accounts:[/cyan]")
    for i, aid in enumerate(creds["account_ids"], 1):
        console.print(f"  {i}. {aid}")
    acct_idx = int(Prompt.ask("Select source account",
                               choices=[str(i) for i in range(1, len(creds["account_ids"]) + 1)])) - 1
    src_account = creds["account_ids"][acct_idx]
    src_type = Prompt.ask("Account type", choices=["CHECKING", "SAVINGS", "MONEYMRKT", "CREDITLINE", "CD"], default="CHECKING").upper()

    # Transfer type
    console.print("\n[cyan]Transfer type:[/cyan]")
    console.print("  1. Intrabank (same bank, between your accounts)")
    console.print("  2. ACH (to another bank, 1-3 day settlement)")
    console.print("  3. Wire (same-day, higher fees)")
    xfer_type = Prompt.ask("Select type", choices=["1", "2", "3"])

    amount = float(Prompt.ask("\nAmount (USD)"))
    memo = Prompt.ask("Memo (optional)", default="")

    if xfer_type == "1":
        to_account = Prompt.ask("Destination account ID (same bank)")
        to_type = Prompt.ask("Destination account type", choices=["CHECKING", "SAVINGS", "MONEYMRKT", "CREDITLINE", "CD"], default="CHECKING").upper()

        console.print(Panel(
            f"[bold]Intrabank Transfer[/bold]\n"
            f"From: {src_account} → To: {to_account}\n"
            f"Amount: [green]${amount:,.2f}[/green]",
            title="Confirm", border_style="yellow",
        ))

        if Confirm.ask("[bold yellow]Execute?[/bold yellow]"):
            try:
                if src_bank == "pnc":
                    import pnc_browser
                    result = pnc_browser.transfer_internal(
                        creds["username"], creds["password"],
                        src_account, to_account, amount, memo
                    )
                    display_transfer_result(result, "Intrabank Transfer (Browser)")
                else:
                    result = send_intrabank_transfer(
                        src_bank, creds["username"], creds["password"],
                        src_account, to_account, amount, memo, "", src_type, to_type,
                    )
                    display_transfer_result(result, "Intrabank Transfer")
            except Exception as e:
                console.print(f"[red]Transfer failed: {e}[/red]")

    elif xfer_type == "2":
        direction = Prompt.ask("Direction", choices=["push", "pull"], default="push")
        from_routing = Prompt.ask("Your routing number")
        payees = get_payees()
        if payees:
            choices = list(payees.keys()) + ["manual"]
            console.print("\n[cyan]Saved payees:[/cyan]")
            for k, p in payees.items():
                console.print(f"  - {p['name']} (Routing: {p['routing']}, Account: {p['account']})")
            payee_select = Prompt.ask("Select payee", choices=choices, default="manual")
            if payee_select != "manual":
                selected = payees[payee_select]
                to_routing = selected["routing"]
                to_account = selected["account"]
            else:
                to_routing = Prompt.ask("External routing number")
                to_account = Prompt.ask("External account ID")
        else:
            to_routing = Prompt.ask("External routing number")
            to_account = Prompt.ask("External account ID")
            
        to_type = Prompt.ask("External account type", choices=["CHECKING", "SAVINGS", "MONEYMRKT", "CREDITLINE", "CD"], default="CHECKING").upper()

        is_pull = (direction == "pull")
        dir_title = "ACH Pull (Debit)" if is_pull else "ACH Push (Credit)"

        console.print(Panel(
            f"[bold]{dir_title} Transfer[/bold]\n"
            f"From (Debited): {to_account if is_pull else src_account} @ {to_routing if is_pull else from_routing}\n"
            f"To (Credited):   {src_account if is_pull else to_account} @ {from_routing if is_pull else to_routing}\n"
            f"Amount: [green]${amount:,.2f}[/green]",
            title="Confirm", border_style="yellow",
        ))

        if Confirm.ask("[bold yellow]Execute?[/bold yellow]"):
            actual_from_acct = to_account if is_pull else src_account
            actual_from_rout = to_routing if is_pull else from_routing
            actual_from_type = to_type if is_pull else src_type

            actual_to_acct = src_account if is_pull else to_account
            actual_to_rout = from_routing if is_pull else to_routing
            actual_to_type = src_type if is_pull else to_type

            try:
                if src_bank == "pnc":
                    if is_pull:
                        console.print("[red]ACH Pull (Debit) is not supported for PNC via browser automation.[/red]")
                        return
                    to_name = ""
                    for p in get_payees().values():
                        if p["account"] == actual_to_acct and p["routing"] == actual_to_rout:
                            to_name = p["name"]
                            break
                    if not to_name:
                        to_name = Prompt.ask("Enter recipient name (must match name registered in PNC external accounts)", default="External Recipient")
                    
                    import pnc_browser
                    result = pnc_browser.transfer_external(
                        creds["username"], creds["password"],
                        actual_from_acct, actual_to_rout, actual_to_acct, to_name, amount, memo
                    )
                    display_transfer_result(result, "ACH Push (Browser)")
                else:
                    result = send_interbank_transfer(
                        src_bank, creds["username"], creds["password"],
                        actual_from_acct, actual_from_rout,
                        actual_to_acct, actual_to_rout,
                        amount, memo, actual_from_type, actual_to_type,
                    )
                    display_transfer_result(result, dir_title)
            except Exception as e:
                console.print(f"[red]Transfer failed: {e}[/red]")

    elif xfer_type == "3":
        if not BANK_PROFILES[src_bank].get("supports_wire"):
            console.print(f"[red]{BANK_PROFILES[src_bank]['name']} doesn't support OFX wire transfers.[/red]")
            return
        from_routing = Prompt.ask("Your routing number")
        payees = get_payees()
        if payees:
            choices = list(payees.keys()) + ["manual"]
            console.print("\n[cyan]Saved payees:[/cyan]")
            for k, p in payees.items():
                console.print(f"  - {p['name']} (Routing: {p['routing']}, Account: {p['account']})")
            payee_select = Prompt.ask("Select payee", choices=choices, default="manual")
            if payee_select != "manual":
                selected = payees[payee_select]
                to_routing = selected["routing"]
                to_account = selected["account"]
                payee = selected["name"]
            else:
                to_routing = Prompt.ask("Destination routing number")
                to_account = Prompt.ask("Destination account ID")
                payee = Prompt.ask("Recipient name")
        else:
            to_routing = Prompt.ask("Destination routing number")
            to_account = Prompt.ask("Destination account ID")
            payee = Prompt.ask("Recipient name")

        console.print(Panel(
            f"[bold]Wire Transfer[/bold]\n"
            f"From: {src_account} @ {from_routing}\n"
            f"To:   {payee} — {to_account} @ {to_routing}\n"
            f"Amount: [green]${amount:,.2f}[/green]\n"
            f"[bold red]⚠ Non-reversible[/bold red]",
            title="Confirm", border_style="red",
        ))

        if Confirm.ask("[bold red]Execute?[/bold red]"):
            result = send_wire_transfer(
                src_bank, creds["username"], creds["password"],
                src_account, from_routing, to_account, to_routing, amount, payee, memo, src_type,
            )
            display_transfer_result(result, "Wire Transfer")


@cli.command()
def status():
    """Show which banks are configured."""
    console.print(BANNER)
    table = Table(title="Bank Configuration Status", box=box.ASCII)
    table.add_column("Bank", style="cyan")
    table.add_column("Status", style="white")
    table.add_column("Accounts", style="green")
    table.add_column("Wire Support", style="yellow")

    for key, profile in BANK_PROFILES.items():
        creds = get_credentials(key)
        if creds:
            if not creds.get("username") or not creds.get("password"):
                status_str = "[yellow][~] Configured (ACH-only)[/yellow]"
            else:
                status_str = "[green][+] Configured[/green]"
            accts = ", ".join(creds.get("account_ids", []))
        else:
            status_str = "[red][-] Not configured[/red]"
            accts = "N/A"
        wire = "[green]Yes[/green]" if profile.get("supports_wire") else "[red]No[/red]"
        table.add_row(profile["name"], status_str, accts, wire)

    console.print(table)
    console.print("\n[dim]Run 'python main.py setup <bank>' to configure a bank.[/dim]")


@cli.command()
@click.argument("name")
@click.argument("routing")
@click.argument("account")
def add_payee(name, routing, account):
    """Add a payee (external routing/account number) for quick transfers."""
    store_payee(name, routing, account)


@cli.command()
@click.argument("name")
def remove_payee(name):
    """Remove a saved payee."""
    delete_payee(name)


@cli.command()
def payees():
    """List all saved payees (external accounts)."""
    console.print(BANNER)
    all_payees = get_payees()
    if not all_payees:
        console.print("[yellow]No saved payees found.[/yellow]")
        console.print("[dim]Run 'python main.py add-payee <name> <routing> <account>' to add one.[/dim]")
        return

    table = Table(title="Saved Payees / External Accounts", box=box.ASCII)
    table.add_column("Name", style="cyan")
    table.add_column("Routing Number", style="green")
    table.add_column("Account Number", style="yellow")
    for k, p in all_payees.items():
        table.add_row(p["name"], p["routing"], p["account"])
    console.print(table)


@cli.command()
@click.argument("bank", type=click.Choice(BANK_CHOICES, case_sensitive=False))
@click.option("--account", "-a", default=None, help="Specific account ID (default: first stored)")
@click.option("--days", "-d", default=30, type=int, help="Number of days of history to fetch (default: 30)")
@click.option("--routing", "-r", default="", help="Routing number override")
@click.option("--type", "acct_type", default="CHECKING", help="Account type: CHECKING, SAVINGS, etc.")
def history(bank: str, account: str, days: int, routing: str, acct_type: str):
    """Fetch and display transaction history."""
    bank = bank.lower()
    creds = _get_creds_or_fail(bank)
    if not creds:
        return

    if creds.get("is_ach_only") or not creds.get("username") or not creds.get("password"):
        console.print(f"\n[yellow]Note: {BANK_PROFILES[bank]['name']} is configured as ACH-only (no login credentials).[/yellow]")
        console.print("[red]Real-time transaction history queries via the OFX server are not supported without online banking credentials.[/red]")
        return

    acct_id = account or creds["account_ids"][0]
    console.print(f"\n[bold]Fetching history for account {acct_id} ({days} days)...[/bold]")

    try:
        txs = get_history(bank, creds["username"], creds["password"], acct_id, days, routing, acct_type)
        display_history(txs)
    except Exception as e:
        console.print(f"[red]Error fetching transaction history: {e}[/red]")


@cli.command()
@click.argument("file_path", type=click.Path(exists=True, dir_okay=False))
def parse(file_path: str):
    """Parse a local downloaded .ofx or .qfx file and display transactions without login."""
    console.print(f"\n[bold]Parsing local statement file: {file_path}[/bold]")
    try:
        txs = parse_local_file(file_path)
        display_history(txs)
    except Exception as e:
        console.print(f"[red]Error parsing file: {e}[/red]")
def manage_payees_submenu():
    while True:
        console.print("\n[bold]Payee Management[/bold]\n")
        console.print("  1. List Saved Payees")
        console.print("  2. Add Saved Payee")
        console.print("  3. Remove Saved Payee")
        console.print("  4. Back to Main Menu")

        choice = Prompt.ask("\nSelect payee option", choices=["1", "2", "3", "4"])
        if choice == "4":
            break

        if choice == "1":
            payees.callback()
        elif choice == "2":
            name = Prompt.ask("Payee Name")
            routing = Prompt.ask("Routing Number")
            account = Prompt.ask("Account Number")
            if name and routing and account:
                add_payee.callback(name, routing, account)
            else:
                console.print("[red]All fields are required.[/red]")
        elif choice == "3":
            all_payees = get_payees()
            if not all_payees:
                console.print("[yellow]No saved payees found.[/yellow]")
                continue
            choices = list(all_payees.keys())
            console.print("\n[cyan]Select payee to remove:[/cyan]")
            for i, k in enumerate(choices, 1):
                console.print(f"  {i}. {all_payees[k]['name']}")
            idx = int(Prompt.ask("Select payee", choices=[str(i) for i in range(1, len(choices) + 1)])) - 1
            remove_payee.callback(choices[idx])


def interactive_menu():
    while True:
        console.print(BANNER)
        console.print("[bold]Main Menu[/bold]\n")
        console.print("  1. Check Account Balance")
        console.print("  2. Transfer Money (Wizard)")
        console.print("  3. View Transaction History")
        console.print("  4. List Bank Accounts")
        console.print("  5. View Configuration Status")
        console.print("  6. Configure Bank Credentials")
        console.print("  7. Manage Saved Payees")
        console.print("  8. Parse Local OFX/QFX File")
        console.print("  9. Remove Bank Configuration")
        console.print("  10. Exit")

        choice = Prompt.ask("\nSelect an option", choices=[str(i) for i in range(1, 11)])

        if choice == "10":
            console.print("[yellow]Goodbye![/yellow]")
            break

        try:
            if choice == "1":
                console.print("\n[cyan]Select bank for balance check:[/cyan]")
                for i, key in enumerate(BANK_CHOICES, 1):
                    console.print(f"  {i}. {BANK_PROFILES[key]['name']}")
                b_idx = int(Prompt.ask("Bank", choices=[str(i) for i in range(1, len(BANK_CHOICES) + 1)])) - 1
                bank = BANK_CHOICES[b_idx]
                creds = get_credentials(bank)
                if not creds:
                    console.print(f"[red]No credentials stored for {BANK_PROFILES[bank]['name']}. Please configure it first.[/red]")
                    continue
                acct_choices = creds.get("account_ids", [])
                if not acct_choices:
                    console.print("[red]No accounts configured for this bank. Please run setup.[/red]")
                    continue
                console.print(f"\n[cyan]Select account (default: {acct_choices[0]}):[/cyan]")
                for i, aid in enumerate(acct_choices, 1):
                    console.print(f"  {i}. {aid}")
                acct_idx_str = Prompt.ask("Account (Enter to select first)", choices=[str(i) for i in range(1, len(acct_choices) + 1)], default="1")
                account = acct_choices[int(acct_idx_str) - 1]
                acct_type = Prompt.ask("Account type", choices=["CHECKING", "SAVINGS", "MONEYMRKT", "CREDITLINE", "CD"], default="CHECKING").upper()
                balance.callback(bank, account, "", acct_type)

            elif choice == "2":
                transfer.callback()

            elif choice == "3":
                console.print("\n[cyan]Select bank for transaction history:[/cyan]")
                for i, key in enumerate(BANK_CHOICES, 1):
                    console.print(f"  {i}. {BANK_PROFILES[key]['name']}")
                b_idx = int(Prompt.ask("Bank", choices=[str(i) for i in range(1, len(BANK_CHOICES) + 1)])) - 1
                bank = BANK_CHOICES[b_idx]
                creds = get_credentials(bank)
                if not creds:
                    console.print(f"[red]No credentials stored for {BANK_PROFILES[bank]['name']}.[/red]")
                    continue
                acct_choices = creds.get("account_ids", [])
                if not acct_choices:
                    console.print("[red]No accounts configured. Please run setup.[/red]")
                    continue
                console.print(f"\n[cyan]Select account (default: {acct_choices[0]}):[/cyan]")
                for i, aid in enumerate(acct_choices, 1):
                    console.print(f"  {i}. {aid}")
                acct_idx_str = Prompt.ask("Account", choices=[str(i) for i in range(1, len(acct_choices) + 1)], default="1")
                account = acct_choices[int(acct_idx_str) - 1]
                days = int(Prompt.ask("Number of days of history to fetch", default="30"))
                acct_type = Prompt.ask("Account type", choices=["CHECKING", "SAVINGS", "MONEYMRKT", "CREDITLINE", "CD"], default="CHECKING").upper()
                history.callback(bank, account, days, "", acct_type)

            elif choice == "4":
                console.print("\n[cyan]Select bank to list accounts:[/cyan]")
                for i, key in enumerate(BANK_CHOICES, 1):
                    console.print(f"  {i}. {BANK_PROFILES[key]['name']}")
                b_idx = int(Prompt.ask("Bank", choices=[str(i) for i in range(1, len(BANK_CHOICES) + 1)])) - 1
                bank = BANK_CHOICES[b_idx]
                accounts.callback(bank)

            elif choice == "5":
                status.callback()

            elif choice == "6":
                console.print("\n[cyan]Select bank to configure:[/cyan]")
                for i, key in enumerate(BANK_CHOICES, 1):
                    console.print(f"  {i}. {BANK_PROFILES[key]['name']}")
                b_idx = int(Prompt.ask("Bank", choices=[str(i) for i in range(1, len(BANK_CHOICES) + 1)])) - 1
                bank = BANK_CHOICES[b_idx]
                setup.callback(bank)

            elif choice == "7":
                manage_payees_submenu()

            elif choice == "8":
                file_path = Prompt.ask("Enter local OFX/QFX file path")
                if not os.path.exists(file_path):
                    console.print(f"[red]File does not exist: {file_path}[/red]")
                    continue
                parse.callback(file_path)

            elif choice == "9":
                console.print("\n[cyan]Select bank configuration to remove:[/cyan]")
                for i, key in enumerate(BANK_CHOICES, 1):
                    console.print(f"  {i}. {BANK_PROFILES[key]['name']}")
                b_idx = int(Prompt.ask("Bank", choices=[str(i) for i in range(1, len(BANK_CHOICES) + 1)])) - 1
                bank = BANK_CHOICES[b_idx]
                remove.callback(bank)

        except Exception as e:
            console.print(f"[red]An error occurred: {e}[/red]")

        input("\nPress Enter to return to main menu...")


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 1:
        interactive_menu()
    else:
        cli()
