import re
import socket
from typing import Tuple, Optional

# Attempt to import dnspython; fallback gracefully if not installed
try:
    import dns.resolver
    HAS_DNSPYTHON = True
except ImportError:
    HAS_DNSPYTHON = False

# Curated list of known temporary/disposable/trash email domains
DISPOSABLE_DOMAINS = {
    # 10 Minute Mail variations
    "10minutemail.com", "10minutemail.net", "10minutemail.org", "10minutemail.co.uk",
    "10minemail.com", "10mail.org", "10minutemail.de",
    # TempMail variations
    "tempmail.com", "temp-mail.org", "tempmailo.com", "tempmail.net", "tempmail.de",
    "temp-mail.io", "tempmailgen.com", "tempmailaddress.com", "tempail.com",
    # Guerrilla Mail variations
    "guerrillamail.com", "guerrillamail.net", "guerrillamail.org", "guerrillamail.biz",
    "guerrillamail.de", "guerrillamailblock.com", "sharklasers.com", "grr.la",
    "pokemail.net", "spam4.me",
    # TrashMail variations
    "trashmail.com", "trashmail.net", "trashmail.org", "trashmail.me", "trashmail.ws",
    "trashinbox.com",
    # Mailinator & Yopmail
    "mailinator.com", "mailinator2.com", "yopmail.com", "yopmail.fr", "yopmail.net",
    "cool.fr.nf", "jetable.fr.nf", "courriel.fr.nf", "moncourrier.fr.nf",
    # Dispostable & Fakeinbox
    "dispostable.com", "fakeinbox.com", "getairmail.com", "throwawaymail.com",
    "throwawayemailaddress.com", "burnermail.io", "nada.ltd", "getnada.com",
    "inboxbear.com", "mytemp.email", "fakemailgenerator.com", "emailondeck.com",
    "generator.email", "discard.email", "spambox.us", "mytempemail.com",
    "tempinbox.com", "mailnesia.com", "mintemail.com", "jetable.org",
    "dropmail.me", "clipmail.eu", "mohmal.com", "crazymailing.com",
    "disposablemail.com", "zillamail.com", "guerrillamail.info", "boun.cr",
    "maildrop.cc", "harakirimail.com", "crazymail.com", "armyspy.com",
    "cuvox.de", "dayrep.com", "fleckens.hu", "gustr.com", "jourrapide.com",
    "rhyta.com", "superrito.com", "teleworm.us", "einrot.com"
}

# Substring patterns that strongly indicate disposable services
DISPOSABLE_KEYWORDS = (
    "tempmail", "10minute", "guerrillamail", "trashmail", "fakeinbox",
    "throwaway", "disposable", "burnermail", "mailinator", "yopmail"
)

# Common domain typos mapped to correct authoritative domains
DOMAIN_TYPOS = {
    "gmai.com": "gmail.com",
    "gamil.com": "gmail.com",
    "gmial.com": "gmail.com",
    "gmaill.com": "gmail.com",
    "gmaik.com": "gmail.com",
    "gemail.com": "gmail.com",
    "gmeil.com": "gmail.com",
    "hotmial.com": "hotmail.com",
    "hotmai.com": "hotmail.com",
    "hotmil.com": "hotmail.com",
    "hotmaill.com": "hotmail.com",
    "homail.com": "hotmail.com",
    "outlok.com": "outlook.com",
    "outloo.com": "outlook.com",
    "outllok.com": "outlook.com",
    "otlook.com": "outlook.com",
    "yaoo.com": "yahoo.com",
    "yaho.com": "yahoo.com",
    "yahooo.com": "yahoo.com",
    "iclod.com": "icloud.com",
    "icoud.com": "icloud.com",
    "iclou.com": "icloud.com",
    "iclud.com": "icloud.com",
    "web.ed": "web.de",
    "gmx.ed": "gmx.de",
    "t-onlin.de": "t-online.de",
    "tonline.de": "t-online.de",
    "protonmail.co": "protonmail.com",
    "protomail.com": "proton.me",
}

EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$"
)


def suggest_email_typo(email: str) -> Optional[str]:
    """
    Checks if the email domain matches a known typo and returns a corrected suggestion.
    """
    if not email or "@" not in email:
        return None
    parts = email.strip().lower().split("@")
    if len(parts) != 2:
        return None
    user, domain = parts[0], parts[1]
    if domain in DOMAIN_TYPOS:
        return f"{user}@{DOMAIN_TYPOS[domain]}"
    return None


def is_disposable_domain(domain: str) -> bool:
    """
    Checks if a domain is a known disposable/trash email provider.
    """
    clean_domain = domain.strip().lower()
    if clean_domain in DISPOSABLE_DOMAINS:
        return True
    # Subdomain check (e.g. mail.mailinator.com)
    for disp in DISPOSABLE_DOMAINS:
        if clean_domain.endswith(f".{disp}"):
            return True
    # Keyword check
    for kw in DISPOSABLE_KEYWORDS:
        if kw in clean_domain:
            return True
    return False


def check_domain_mail_records(domain: str, timeout: float = 3.0) -> Tuple[bool, str]:
    """
    Verifies that the domain exists and can receive mail via MX (or A) records.
    Uses dnspython if available, otherwise falls back to standard socket.getaddrinfo.
    """
    clean_domain = domain.strip().lower()
    if not clean_domain or "." not in clean_domain:
        return False, "Invalid domain format."

    if HAS_DNSPYTHON:
        try:
            resolver = dns.resolver.Resolver()
            resolver.lifetime = timeout
            resolver.timeout = timeout
            # Try MX record first
            try:
                mx_answers = resolver.resolve(clean_domain, "MX")
                if mx_answers and len(mx_answers) > 0:
                    return True, "Valid MX records found."
            except (dns.resolver.NoAnswer, dns.resolver.NoNameservers):
                # RFC 5321 fallback: If no MX, check for A/AAAA record
                try:
                    a_answers = resolver.resolve(clean_domain, "A")
                    if a_answers and len(a_answers) > 0:
                        return True, "Valid A record found (fallback)."
                except Exception:
                    return False, f"Domain '{clean_domain}' has no mail server (MX/A) records."
            except dns.resolver.NXDOMAIN:
                return False, f"The domain '{clean_domain}' does not exist."
            except dns.exception.Timeout:
                return False, f"DNS lookup timed out for '{clean_domain}'."
            except Exception as e:
                return False, f"DNS lookup failed for '{clean_domain}': {str(e)}"
        except Exception as e:
            # If resolver initialization fails, fallback to socket
            pass

    # Standard library fallback if dnspython is missing or failed
    try:
        socket.setdefaulttimeout(timeout)
        socket.getaddrinfo(clean_domain, None)
        return True, "Domain resolved via standard socket."
    except socket.gaierror:
        return False, f"The domain '{clean_domain}' does not exist or cannot be resolved."
    except Exception as e:
        return False, f"Domain check failed for '{clean_domain}': {str(e)}"


def validate_email_deliverability(email: str) -> Tuple[bool, str]:
    """
    Performs full deliverability validation:
    1. Syntax check
    2. Disposable domain filter
    3. DNS MX/A record verification
    
    Returns (is_valid: bool, error_message: str)
    """
    clean_email = (email or "").strip().lower()
    
    # 1. Syntax check
    if not clean_email or not EMAIL_REGEX.match(clean_email):
        return False, "Please enter a valid email address."
    
    parts = clean_email.split("@")
    if len(parts) != 2:
        return False, "Please enter a valid email address."
    
    domain = parts[1]
    
    # 2. Disposable domain check
    if is_disposable_domain(domain):
        return False, "Temporary or disposable email addresses are not allowed. Please use your permanent email address."
    
    # 3. DNS MX/A check
    has_records, msg = check_domain_mail_records(domain)
    if not has_records:
        return False, msg
    
    return True, "Email address is valid and deliverable."
