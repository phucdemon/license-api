from flask import Flask, request, jsonify
from hashlib import sha256

app = Flask(__name__)

VALID_LICENSES = {
    sha256(b"ABC123").hexdigest(),
    sha256(b"XYZ456").hexdigest(),
}

@app.route("/verify", methods=["POST"])
def verify():
    data = request.get_json()
    license_hash = data.get("hash")
    if license_hash in VALID_LICENSES:
        return jsonify({"valid": True})
    return jsonify({"valid": False})

if __name__ == "__main__":
    app.run()
