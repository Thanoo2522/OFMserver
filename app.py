from flask import Flask, request, jsonify, send_file, send_from_directory
import os
import base64
from datetime import datetime
import traceback
import uuid
import json
import time
import requests
import firebase_admin
from firebase_admin import credentials, storage, db as rtdb, firestore
 
import io
from io import BytesIO

app = Flask(__name__)

# ------------------- Config -------------------get_user
RTD_URL1 = "https://retailstore-4780f-default-rtdb.asia-southeast1.firebasedatabase.app"
BUCKET_NAME = "retailstore-4780f.firebasestorage.app"
#--------
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
UPLOAD_ROOT = "storage_folders"   # ← ต้องมีตัวนี้
os.makedirs(UPLOAD_ROOT, exist_ok=True)
#-------
service_account_json = os.environ.get("FIREBASE_SERVICE_KEY")
cred = credentials.Certificate(json.loads(service_account_json))
firebase_admin.initialize_app(cred, {"storageBucket": BUCKET_NAME,"databaseURL": RTD_URL1})

db = firestore.client()
rtdb_ref = rtdb.reference("/") # ← Realtime Database root
bucket = storage.bucket()
#--------------------------------------------------------------------------------------
# 🔹 สร้าง document system/way และตั้งค่า connected="true"
@app.route("/create_connection", methods=["POST"])
def create_connection():
    try:
        doc_ref = db.collection("system").document("connection")
        doc_ref.set({
            "connected": "true"
        })
        return jsonify({
            "status": "success",
            "message": "Created document system/way with connected=true"
        }), 200
    except Exception as e:
        print("❌ Error:", e)
        return jsonify({"status": "error", "message": str(e)}), 500    
# ---------------- เช็คการเชื่อมต่อกับ firestore database ----------------
@app.route("/check_connection", methods=["GET"])
def check_connection():
    try:
        # 🔹 เข้าถึง document system/way
        doc_ref = db.collection("system").document("connection")
        doc = doc_ref.get()

        if not doc.exists:
            return jsonify({"status": "error", "message": "Document not found"}), 404

        data = doc.to_dict()
        connected = data.get("connected", "false")

        if connected == "true":
            return jsonify({"status": "success", "connected": True})
        else:
            return jsonify({"status": "success", "connected": False})

    except Exception as e:
        print("❌ Error:", e)
        return jsonify({"status": "error", "message": str(e)}), 500
#-----------------------------------------------------------
    #------------------------- ดึงภาพทำสไลค์ที่ storage --------------
@app.route('/get_view_list', methods=['GET'])
def get_view_list():
    try:
        bucket = storage.bucket()
        blobs = bucket.list_blobs(prefix="modeproduct/")

        filenames = [
            blob.name.replace("modeproduct/", "") 
            for blob in blobs 
            if blob.name.replace("modeproduct/", "") != ""  # กรองค่าว่าง
        ]

        return jsonify(filenames)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
 #-------------------------------------------------------------
@app.route('/modeproduct/<filename>', methods=['GET'])
def get_modeproduct_image(filename):
    try:
        blob = bucket.blob(f"modeproduct/{filename}")
        image_data = blob.download_as_bytes()

        return send_file(
            io.BytesIO(image_data),
            mimetype='image/jpeg'
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500
# ---------------- API สำหรับสร้างโฟลเดอร์ ----------------
@app.route("/upload_image_with_folder", methods=["POST"])
def upload_image_with_folder():
    try:
        folder_name = request.form.get("folder_name")
        image_file = request.files.get("image_file")

        if not folder_name:
            return jsonify({"status": "error", "message": "ต้องส่ง folder_name"}), 400

        if not image_file:
            return jsonify({"status": "error", "message": "ต้องส่ง image_file"}), 400

        # ตั้งชื่อไฟล์ไม่ซ้ำ
        #filename = datetime.now().strftime("%Y%m%d_%H%M%S") + ".jpg"
        filename = f"{folder_name}.jpg"

        # path ใน Firebase Storage
        blob_path = f"{folder_name}/{filename}"

        # อัปโหลดไป Firebase
        blob = bucket.blob(blob_path)
        blob.upload_from_file(image_file, content_type="image/jpeg")

        # ให้ URL สำหรับโหลดกลับไป MAUI
        blob.make_public()
        download_url = blob.public_url

        return jsonify({
            "status": "success",
            "message": f"อัปโหลดขึ้น Firebase สำเร็จ: {blob_path}",
            "url": download_url
        })

    except Exception as e:
        print("🔥 ERROR:", e)
        return jsonify({"status": "error", "message": str(e)}), 500
        #-----------------------------------------------------
@app.route("/register_shop", methods=["POST"])
def register_shop():
    try:
        data = request.get_json()

        shopname = data.get("shopname")
        phone = data.get("phone")
        password = data.get("password")   # ใช้เป็นชื่อ document

        if not shopname or not phone or not password:
            return jsonify({"status": "error", "message": "กรอกข้อมูลไม่ครบ"}), 400

        # --------------------------------------------
        # เก็บข้อมูลใน Firestore
        # Collection: Shopname
        # Document ID: password
        # Fields: shopname, phone
        # --------------------------------------------

        doc_ref = db.collection("Shopname").document(password)

        doc_ref.set({
            "shopname": shopname,
            "phone": phone,
            "password":password
        })

        return jsonify({
            "status": "success",
            "message": "บันทึกข้อมูลสำเร็จ"
        })

    except Exception as e:
        print("🔥 ERROR:", e)
        return jsonify({"status": "error", "message": str(e)}), 500
    #------------------------------------------------
