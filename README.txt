KinoTime starter project (Flask)

LOCAL TEST:
1. Install Python 3.10+.
2. In this folder run: pip install -r requirements.txt
3. Set ADMIN_USERNAME and ADMIN_PASSWORD environment variables (recommended).
4. Set SECRET_KEY to a long random secret.
5. Run: python app.py
6. Open http://127.0.0.1:5000

Default local admin if not configured: admin / ChangeMe123!
Change this before any deployment. Do not deploy with the default secret or password.

IMPORTANT LIMITATIONS BEFORE PUBLIC LAUNCH:
- This is a starter app, not yet a production-ready public service.
- QPay is NOT connected. QPay merchant/API credentials and server-side callback verification are required.
- Bank transfer is manual: the user submits proof; admin approves/rejects it.
- Configure the real KHAN Bank account and QPay QR details in the payment page before accepting money.
- Use HTTPS, a production WSGI server, persistent database/storage, backups, file-size limits, and security review.
- Uploading large video files to this app requires suitable hosting/storage and upload limits. External video embeds must be permitted by the source site.
- Only upload or embed content you have rights to distribute.
