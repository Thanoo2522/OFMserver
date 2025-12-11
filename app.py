from flask import Flask, request, jsonify, send_file
import os
import base64
import traceback
import json
import io
from io import BytesIO
import firebase_admin
from firebase_admin import credentials, storage, db as rtdb, firestore
from openai import OpenAI
import mimetypes
import tempfile

from PIL import Image
 
 

app = Flask(__name__)

# ------------------- Config -------------------
RTD_URL1 = "https://retailstore-4780f-default-rtdb.asia-southeast1.firebasedatabase.app"
BUCKET_NAME = "retailstore-4780f.firebasestorage.app"

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

service_account_json = os.environ.get("FIREBASE_SERVICE_KEY")
cred = credentials.Certificate(json.loads(service_account_json))

firebase_admin.initialize_app(
    cred,
    {"storageBucket": BUCKET_NAME, "databaseURL": RTD_URL1}
)

db = firestore.client()
rtdb_ref = rtdb.reference("/")
bucket = storage.bucket()

# ---------------- OpenAI -------------------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)


# --------------------------- IMAGE EDIT ---------------------------
@app.route("/edit_image", methods=["POST"])
def edit_image():
    try:
        if "image" not in request.files:
            return jsonify({"error": "Missing file 'image'"}), 400

        image_file = request.files["image"]

        # -----------------------------------------------------------
        # 1) เปิดภาพด้วย Pillow (ทำสำเนา new image + white background)
        # -----------------------------------------------------------
        img = Image.open(image_file.stream).convert("RGBA")

        # ขนาดภาพเดิม
        w, h = img.size

        # เพิ่มขอบรอบด้าน (หน่วย: px)
        border = 100  # ปรับได้ตามต้องการ

        new_w = w + border * 2
        new_h = h + border * 2

        # สร้างภาพใหม่พื้นหลังขาวล้วน
        white_bg = Image.new("RGBA", (new_w, new_h), (255, 255, 255, 255))

        # วางภาพเดิมไว้ตรงกลาง
        white_bg.paste(img, (border, border), img if img.mode == "RGBA" else None)

        # แปลงกลับเป็น RGB (ไม่ต้องใช้ alpha)
        final_img = white_bg.convert("RGB")

        # -----------------------------------------------------------
        # 2) แปลงเป็น bytes เพื่อส่งต่อให้ gpt-image-1
        # -----------------------------------------------------------
        buffer = BytesIO()
        final_img.save(buffer, format="JPEG")
        buffer.seek(0)

        # -----------------------------------------------------------
        # 3) ส่งภาพให้ GPT แต่ง (พื้นหลังจะเป็นสีขาวแน่นอน)
        # -----------------------------------------------------------
        edited = client.images.edit(
            model="gpt-image-1",
            image=("image.jpg", buffer, "image/jpeg"),
            prompt="keep subject exactly the same, pure white background, clean edges, high clarity, balanced lighting",
            size="1024x1024"
        )

        # -----------------------------------------------------------
        # 4) ส่งภาพกลับให้ MAUI
        # -----------------------------------------------------------
        result_bytes = base64.b64decode(edited.data[0].b64_json)

        return send_file(
            BytesIO(result_bytes),
            mimetype="image/png",
            as_attachment=False
        )

    except Exception as e:
        print("❌ ERROR in /edit_image:", e)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    #-----------------------------
@app.route('/get_view_list', methods=['GET'])
def get_view_list():
    try:
        bucket = storage.bucket()
        blobs = bucket.list_blobs(prefix="modeproduct/")

        filenames = [
            blob.name.replace("modeproduct/", "")
            for blob in blobs
            if blob.name.replace("modeproduct/", "") != ""
        ]

        return jsonify(filenames)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    #---------------------------------------------
@app.route('/image_view/<filename>', methods=['GET'])
def image_view(filename):
    try:
        bucket = storage.bucket()
        blob = bucket.blob(f"modeproduct/{filename}")

        if not blob.exists():
            return jsonify({"error": "File not found"}), 404

        temp_file = tempfile.NamedTemporaryFile(delete=False)
        temp_path = temp_file.name
        temp_file.close()

        blob.download_to_filename(temp_path)

        ext = filename.lower().split('.')[-1]
        mimetype = f"image/{'jpeg' if ext == 'jpg' else ext}"

        return send_file(temp_path, mimetype=mimetype, as_attachment=False)

    except Exception as e:
        return jsonify({"error": str(e)}), 500



# --------------------------- Firebase APIS ---------------------------
@app.route("/upload_image_with_folder", methods=["POST"])
def upload_image_with_folder():
    try:
        folder_name = request.form.get("folder_name")
        file = request.files.get("image_file")

        if not folder_name:
            return jsonify({"status": "error", "message": "folder_name missing"}), 400

        if not file:
            return jsonify({"status": "error", "message": "image_file missing"}), 400

        filename = f"{folder_name}.jpg"
        path = f"{folder_name}/{filename}"

        blob = bucket.blob(path)
        blob.upload_from_file(file, content_type="image/jpeg")
        blob.make_public()

        return jsonify({
            "status": "success",
            "url": blob.public_url,
            "path": path
        })

    except Exception as e:
        print("🔥 ERROR:", e)
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


# --------------------------- Login/Register --------------------------
@app.route("/register_shop", methods=["POST"])
def register_shop():
    try:
        data = request.get_json()
        shopname = data.get("shopname")
        phone = data.get("phone")
        password = data.get("password")

        if not shopname or not phone or not password:
            return jsonify({"status": "error", "message": "Missing fields"}), 400

        doc_ref = db.collection("Shopname").document(password)
        doc_ref.set({"shopname": shopname, "phone": phone, "password": password})

        return jsonify({"status": "success", "message": "Saved"}), 200

    except Exception as e:
        print("🔥 ERROR:", e)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/check_password", methods=["POST"])
def check_password():
    try:
        data = request.get_json()
        input_password = data.get("password")

        if not input_password:
            return jsonify({"status": "error", "message": "Missing password"}), 400

        doc_ref = db.collection("Shopname").document(input_password)
        doc = doc_ref.get()

        if doc.exists:
            return jsonify({"status": "success", "message": "Login OK"})
        else:
            return jsonify({"status": "error", "message": "Not registered"})

    except Exception as e:
        print("ERROR:", e)
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
