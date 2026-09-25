"""
Credential manager — stores/retrieves bank credentials via OS keyring.
Never writes plaintext creds to disk.
"""

import json
import keyring
from config import KEYRING_SERVICE, BANK_PROFILES
from rich.console import Console
from rich.prompt import Prompt

console = Console()


def store_credentials(bank_key: str, username: str, password: str, account_ids: list[str], routing: str = "", is_ach_only: bool = False):
    """Store credentials in OS keyring under a per-bank key."""
    payload = json.dumps({
        "username": username,
        "password": password,
        "account_ids": account_ids,
        "routing": routing,
        "is_ach_only": is_ach_only,
    })
    keyring.set_password(KEYRING_SERVICE, bank_key, payload)
    if is_ach_only:
        console.print(f"[green][+][/green] ACH-only configuration for {BANK_PROFILES[bank_key]['name']} stored in OS keyring.")
    else:
        console.print(f"[green][+][/green] Credentials for {BANK_PROFILES[bank_key]['name']} stored in OS keyring.")


def get_credentials(bank_key: str) -> dict | None:
    """Retrieve stored credentials for a bank."""
    raw = keyring.get_password(KEYRING_SERVICE, bank_key)
    if raw is None:
        return None
    return json.loads(raw)


def delete_credentials(bank_key: str):
    """Remove stored credentials for a bank."""
    try:
        keyring.delete_password(KEYRING_SERVICE, bank_key)
        console.print(f"[yellow][*][/yellow] Credentials for {BANK_PROFILES[bank_key]['name']} removed.")
    except keyring.errors.PasswordDeleteError:
        console.print(f"[red][-] No stored credentials found for {BANK_PROFILES[bank_key]['name']}.[/red]")


def prompt_and_store(bank_key: str):
    """Interactive prompt to collect and store credentials."""
    profile = BANK_PROFILES.get(bank_key)
    if not profile:
        console.print(f"[red]Unknown bank key: {bank_key}[/red]")
        return

    console.print(f"\n[bold cyan]Configure {profile['name']}[/bold cyan]")
    console.print("[dim]Credentials are stored in your OS keyring — never written to disk as plaintext.[/dim]\n")

    username = Prompt.ask("  Username / User ID (leave blank for ACH-only)", default="")
    password = ""
    is_ach_only = not username

    if not is_ach_only:
        password = Prompt.ask("  Password", password=True)
        routing = ""
    else:
        routing = Prompt.ask("  Routing Number")

    console.print("\n  Enter your account numbers (checking, savings, etc.).")
    console.print("  Type each account ID and press Enter. Empty line to finish.\n")

    account_ids = []
    while True:
        acct = Prompt.ask("  Account ID (blank to finish)", default="")
        if not acct:
            break
        account_ids.append(acct.strip())

    if not account_ids:
        console.print("[red]At least one account ID is required.[/red]")
        return

    store_credentials(bank_key, username, password, account_ids, routing, is_ach_only)


def get_payees() -> dict:
    """Retrieve saved payees (external accounts) from keyring."""
    raw = keyring.get_password(KEYRING_SERVICE, "external_payees")
    if not raw:
        return {}
    return json.loads(raw)


def store_payee(name: str, routing: str, account: str):
    """Save or update a payee (external account) in the keyring."""
    payees = get_payees()
    payees[name.lower()] = {
        "name": name,
        "routing": routing,
        "account": account,
    }
    keyring.set_password(KEYRING_SERVICE, "external_payees", json.dumps(payees))
    console.print(f"[green][+][/green] Payee '{name}' stored successfully.")


def delete_payee(name: str):
    """Remove a saved payee."""
    payees = get_payees()
    if name.lower() in payees:
        del payees[name.lower()]
        keyring.set_password(KEYRING_SERVICE, "external_payees", json.dumps(payees))
        console.print(f"[yellow][*][/yellow] Payee '{name}' removed.")
    else:
        console.print(f"[red][-] Payee '{name}' not found.[/red]")
