# Creates the site's live Stripe webhook (tools/stripe_webhook.py --create).
#
# Asks for the restricted live key without showing it, sets it for this one run
# only, and clears it afterwards, so the key is never on screen, in the shell's
# history or left in the environment. The script prints neither the key nor the
# webhook's signing secret: the secret goes to .env.stripe-webhook-live
# (gitignored), to paste into Railway as STRIPE_WEBHOOK_SECRET and then delete.
#
#   powershell -ExecutionPolicy Bypass -File tools\stripe_webhook_live.ps1

$secure = Read-Host "Paste the rk_live_ key (nothing will show), then press Enter" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $env:STRIPE_SECRET_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    python "$PSScriptRoot\stripe_webhook.py" --create
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    Remove-Item Env:STRIPE_SECRET_KEY -ErrorAction SilentlyContinue
}
