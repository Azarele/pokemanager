"""
Legal pages: Terms, Privacy, Cookie Policy
"""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

def _page(title: str, content: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} — PokeManager</title>
  <link rel="icon" type="image/png" sizes="32x32" href="/static/icons/favicon-32.png">
  <link rel="icon" type="image/png" sizes="16x16" href="/static/icons/favicon-16.png">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: #0f0f13; color: #e8e8f0; font-family: -apple-system, BlinkMacSystemFont, Inter, sans-serif;
            line-height: 1.7; padding: 40px 20px; }}
    .container {{ max-width: 760px; margin: 0 auto; }}
    h1 {{ font-size: 28px; font-weight: 700; margin-bottom: 8px; color: #6c63ff; }}
    h2 {{ font-size: 18px; font-weight: 600; margin: 32px 0 12px; }}
    p  {{ margin-bottom: 14px; color: #c8c8d8; }}
    a  {{ color: #6c63ff; }}
    .meta {{ color: #888899; font-size: 13px; margin-bottom: 32px; }}
    .back {{ display: inline-block; margin-bottom: 32px; color: #6c63ff; text-decoration: none; font-size: 14px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; margin: 16px 0; }}
    th, td {{ text-align: left; padding: 8px 0; border-bottom: 1px solid #2e2e3e; color: #888899; }}
  </style>
</head>
<body>
  <div class="container">
    <a href="/" class="back">← Back to PokeManager</a>
    <h1>{title}</h1>
    {content}
  </div>
</body>
</html>"""


@router.get("/terms", response_class=HTMLResponse)
async def terms():
    return _page("Terms of Service", """
<p class="meta">Last updated: July 2026 · PokeManager</p>

<h2>1. Acceptance of Terms</h2>
<p>By creating an account and using PokeManager ("the Service"), you agree to these Terms of Service. If you do not agree, do not use the Service.</p>

<h2>2. Description of Service</h2>
<p>PokeManager is an inventory management and listing tool for Pokémon Trading Card Game collectors and resellers. The Service integrates with third-party platforms including eBay and Vinted. PokeManager is not affiliated with, endorsed by, or in any way officially connected with Nintendo, The Pokémon Company, eBay, or Vinted.</p>

<h2>3. Eligibility</h2>
<p>You must be at least 18 years old to use PokeManager. By using the Service you confirm you meet this requirement.</p>

<h2>4. Your Account</h2>
<p>You are responsible for maintaining the security of your account credentials. You must notify us immediately at <a href="mailto:pokemanager@thesneakaz.xyz">pokemanager@thesneakaz.xyz</a> if you suspect unauthorised access. We are not liable for any loss resulting from unauthorised access to your account.</p>

<h2>5. Subscriptions and Billing</h2>
<p>Paid subscriptions (Gym Leader and Champion) are billed monthly in advance via Stripe. By starting a paid subscription, you acknowledge that the service begins immediately and you waive your 14-day statutory cooling-off right under the Consumer Contracts Regulations 2013. All prices are in GBP and inclusive of VAT where applicable.</p>

<h2>6. Refund Policy</h2>
<p>As you waive your cooling-off right on subscription commencement, refunds are not offered. You may cancel your subscription at any time; access continues until the end of your current billing period. No partial refunds are given for unused time.</p>

<h2>7. Acceptable Use</h2>
<p>You agree not to use the Service to list counterfeit or fraudulent items, to violate eBay's or Vinted's terms of service, to attempt to reverse-engineer or scrape the Service, or to engage in any unlawful activity.</p>

<h2>8. Third-Party API Keys</h2>
<p>When you provide your own eBay or Gemini API keys, you are responsible for their security and for complying with the respective platform's terms of service. We store your keys encrypted and do not use them for any purpose other than executing actions you explicitly request.</p>

<h2>9. Limitation of Liability</h2>
<p>PokeManager is provided "as is." To the maximum extent permitted by law, we exclude all liability for loss of profits, loss of data, or any indirect or consequential losses arising from your use of the Service. Our total liability shall not exceed the amount you paid to us in the 3 months preceding the claim.</p>

<h2>10. Changes to Terms</h2>
<p>We may update these terms. We will notify you by email and give 14 days' notice before changes take effect. Continued use after that period constitutes acceptance.</p>

<h2>11. Governing Law</h2>
<p>These terms are governed by the laws of England and Wales. Disputes shall be subject to the exclusive jurisdiction of the courts of England and Wales.</p>

<h2>12. Contact</h2>
<p>For any queries regarding these terms, contact us at <a href="mailto:pokemanager@thesneakaz.xyz">pokemanager@thesneakaz.xyz</a>.</p>
""")


@router.get("/privacy", response_class=HTMLResponse)
async def privacy():
    return _page("Privacy Policy", """
<p class="meta">Last updated: July 2026 · PokeManager</p>

<h2>1. Who We Are</h2>
<p>PokeManager, United Kingdom. Contact: <a href="mailto:pokemanager@thesneakaz.xyz">pokemanager@thesneakaz.xyz</a></p>

<h2>2. Data We Collect</h2>
<p><strong>Account data:</strong> email address, display name, and hashed password when you register.</p>
<p><strong>Inventory data:</strong> card names, prices, purchase details, and sale records you enter into the Service.</p>
<p><strong>API keys:</strong> eBay and Gemini API credentials you optionally provide, stored encrypted at rest.</p>
<p><strong>Billing data:</strong> subscription status and Stripe customer ID. We do not store card numbers — these are handled entirely by Stripe.</p>
<p><strong>Usage data:</strong> server logs including IP address and request timestamps, retained for 30 days for security purposes.</p>

<h2>3. How We Use Your Data</h2>
<p>We use your data solely to provide the Service: managing your inventory, executing listings on connected platforms, and processing your subscription. We do not sell your data to third parties or use it for advertising.</p>

<h2>4. Data Storage</h2>
<p>Your data is stored in the European Union via Supabase (hosted on AWS eu-west-2). Payment data is processed and stored by Stripe, Inc. Both providers maintain appropriate security certifications.</p>

<h2>5. Your Rights (GDPR)</h2>
<p>As a UK/EU resident you have the right to: access your data, correct inaccurate data, delete your account and all associated data, export your data in a portable format, and object to processing. To exercise any right, email <a href="mailto:pokemanager@thesneakaz.xyz">pokemanager@thesneakaz.xyz</a>. We will respond within 30 days.</p>

<h2>6. Cookies</h2>
<p>We use strictly necessary cookies only: an authentication token (httpOnly, expires after 1 hour) and a refresh token (httpOnly, expires after 30 days). These are required for the Service to function. We do not use tracking or advertising cookies. See our <a href="/cookies">Cookie Policy</a> for details.</p>

<h2>7. Data Retention</h2>
<p>We retain your data for as long as your account is active. If you delete your account, all personal data is permanently deleted within 30 days.</p>

<h2>8. Contact</h2>
<p>For privacy queries or to exercise your rights: <a href="mailto:pokemanager@thesneakaz.xyz">pokemanager@thesneakaz.xyz</a></p>
""")


@router.get("/cookies", response_class=HTMLResponse)
async def cookies():
    return _page("Cookie Policy", """
<p class="meta">Last updated: July 2026</p>

<h2>What cookies we use</h2>
<p>PokeManager uses only strictly necessary cookies required for the Service to function. We do not use analytics, advertising, or tracking cookies.</p>

<table>
  <tr>
    <th>Cookie</th>
    <th>Purpose</th>
    <th>Expires</th>
  </tr>
  <tr>
    <td>access_token</td>
    <td>Authentication — keeps you logged in</td>
    <td>1 hour</td>
  </tr>
  <tr>
    <td>refresh_token</td>
    <td>Session renewal — refreshes your access token</td>
    <td>30 days</td>
  </tr>
</table>

<p>These cookies are HttpOnly (not accessible to JavaScript) and are transmitted only over HTTPS in production. They are set when you log in and cleared when you log out.</p>

<h2>How to control cookies</h2>
<p>You can clear these cookies at any time through your browser settings or by logging out of PokeManager. Clearing them will require you to log in again. As these are strictly necessary cookies, they cannot be disabled without preventing the Service from functioning.</p>
""")
