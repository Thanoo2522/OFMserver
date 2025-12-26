from flask import Flask, request, jsonify, send_file
import os, json, base64, traceback, tempfile
from io import BytesIO

import firebase_admin
from firebase_admin import credentials, storage, db as rtdb, firestore

from openai import OpenAI
from PIL import Image
from datetime import datetime

from werkzeug.security import generate_password_hash, check_password_hash
#---------------------------------------
import qrcode
import io
 
INSTALL_URL = "https://jai.app/install"

# ------------------- Flask ----------- 
app = Flask(__name__)

# ------------------- Firebase Config -------------------
RTD_URL1 = "https://retailstore-4780f-default-rtdb.asia-southeast1.firebasedatabase.app"
BUCKET_NAME = "retailstore-4780f.firebasestorage.app"

service_account_json = os.environ.get("FIREBASE_SERVICE_KEY")
if not service_account_json:
    raise RuntimeError("Missing FIREBASE_SERVICE_KEY")

cred = credentials.Certificate(json.loads(service_account_json))

firebase_admin.initialize_app(
    cred,
    {
        "storageBucket": BUCKET_NAME,
        "databaseURL": RTD_URL1
    }
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
        mime = image_file.mimetype or "image/jpeg"

        edited = client.images.edit(
            model="gpt-image-1",
            image=("image.jpg", image_file.stream, mime),
            prompt=(
                "expand canvas on top and bottom with pure white background, "
                "keep original subject unchanged, clean full white background, "
                "sharpen, enhance clarity, improve lighting"
            ),
            size="1024x1024"
        )

        result_bytes = base64.b64decode(edited.data[0].b64_json)

        return send_file(
            BytesIO(result_bytes),
            mimetype="image/png",
            as_attachment=False
        )

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# --------------------------- View Images ---------------------------
@app.route('/get_view_list', methods=['GET'])
def get_view_list():
    try:
        folder = request.args.get("folder")
        if not folder:
            return jsonify({"error": "Missing ?folder="}), 400

        blobs = bucket.list_blobs(prefix=f"{folder}/")
        filenames = [
            blob.name.replace(f"{folder}/", "")
            for blob in blobs
            if "." in blob.name
        ]

        return jsonify(filenames), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
#
# ---------- API : Get all categories ------ 
@app.route("/get_all_categories", methods=["GET"])
def get_all_categories():
    try:
        shopname = request.args.get("shopname")

        if not shopname:
            return jsonify({
                "status": "error",
                "message": "Missing shopname"
            }), 400

        blobs = bucket.list_blobs(prefix=f"{shopname}/")

        categories = {}

        for blob in blobs:
            # ตัวอย่าง blob.name:
            # shop1/mode1/pic11.jpg
            parts = blob.name.split("/")

            # ต้องเป็น shop/mode/file
            if len(parts) != 3:
                continue

            _, mode, filename = parts

            # ข้ามไฟล์ที่ไม่ใช่รูป
            if not filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                continue

            # ✅ ถ้ายังไม่มี mode นี้ → ใช้รูปแรกเป็น thumbnail
            if mode not in categories:
                image_url = (
                    f"https://storage.googleapis.com/"
                    f"{bucket.name}/{blob.name}"
                )
                categories[mode] = image_url

        result = [
            {
                "mode": mode,
                "imageUrl": image_url
            }
            for mode, image_url in categories.items()
        ]

        return jsonify({
            "status": "success",
            "categories": result
        })

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

#

@app.route('/image_view/<folder>/<filename>', methods=['GET'])
def image_view(folder, filename):
    try:
        blob = bucket.blob(f"{folder}/{filename}")
        if not blob.exists():
            return jsonify({"error": "File not found"}), 404

        temp = tempfile.NamedTemporaryFile(delete=False)
        blob.download_to_filename(temp.name)

        ext = filename.lower().split('.')[-1]
        mimetype = f"image/{'jpeg' if ext == 'jpg' else ext}"

        return send_file(temp.name, mimetype=mimetype)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    #-----------------------
@app.route("/update_mode", methods=["POST"])
def update_mode():
    try:
        folder_name = request.form.get("folder_name")
        picturename = request.form.get("picturename")
        file = request.files.get("image_file")

        if not folder_name or not picturename or not file:
            return jsonify({
                "status": "error",
                "message": "Missing fields"
            }), 400

        # 📂 โครงสร้าง: folder_name/picturename
        path = f"{folder_name}/{picturename}"
        blob = bucket.blob(path)

        blob.upload_from_file(
            file,
            content_type=file.mimetype or "image/jpeg"
        )
        blob.make_public()

        return jsonify({
            "status": "success",
            "folder_name": folder_name,
            "path": path,
            "public_url": blob.public_url
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


# --------------------------- Upload Image ---------------
@app.route("/upload_image_with_folder", methods=["POST"])
def upload_image_with_folder():
    try:
        shopname = request.form.get("shopname")
        folder_name = request.form.get("folder_name")
        picturename = request.form.get("picturename")
        file = request.files.get("image_file")

        if not shopname or not folder_name or not picturename or not file:
            return jsonify({
                "status": "error",
                "message": "Missing fields"
            }), 400

        # ===============================
        # 🔹 sanitize + บังคับ .jpg
        # ===============================
        picturename = picturename.strip()

        if not picturename.lower().endswith((".jpg", ".jpeg")):
            picturename = f"{picturename}.jpg"

        # ===============================
        # 📂 path: shopname/folder_name/picturename.jpg
        # ===============================
        path = f"{shopname}/{folder_name}/{picturename}"

        blob = bucket.blob(path)

        # ===============================
        # 🔹 upload + fix content-type
        # ===============================
        blob.upload_from_file(
            file,
            content_type="image/jpeg"
        )

        blob.make_public()

        return jsonify({
            "status": "success",
            "shopname": shopname,
            "folder_name": folder_name,
            "filename": picturename,
            "path": path,
            "public_url": blob.public_url
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


 #-------------------สร้าง โฟลเดอร์ตอนลงทะเบียนใหม่ ----------------- 
@app.route("/create_shop_folder", methods=["POST"])
def create_shop_folder():
    try:
        data = request.get_json()
        shopname = data.get("shopname")

        if not shopname:
            return jsonify({
                "status": "error",
                "message": "Missing shopname"
            }), 400

        # สร้าง blob เปล่าเพื่อให้เกิด folder
        folder_path = f"{shopname}/.keep"
        blob = bucket.blob(folder_path)

        if not blob.exists():
            blob.upload_from_string("", content_type="text/plain")

        return jsonify({
            "status": "success",
            "shopname": shopname,
            "folder": f"{shopname}/"
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

# --------------------------- Register / Login ---------------------------
@app.route("/register_shop", methods=["POST"])
def register_shop():
    data = request.get_json()
    shopname = data.get("shopname")
    phone = data.get("phone")
    password = data.get("password")

    if not shopname or not phone or not password:
        return jsonify({"status": "error", "message": "Missing fields"}), 400

    db.collection("Shopname").document(shopname).set({
        "shopname": shopname,
        "phone": phone,
        "password": password
    })

    return jsonify({"status": "success"}), 200


@app.route("/check_password", methods=["POST"])
def check_password():
    data = request.get_json()
    shopname = data.get("shopname")
    input_password = data.get("password")

    if not shopname or not input_password:
        return jsonify({"status": "error", "message": "Missing fields"}), 400

    doc = db.collection("Shopname").document(shopname).get()

    if not doc.exists:
        return jsonify({"status": "not_found"}), 200

    if doc.to_dict().get("password") == input_password:
        return jsonify({"status": "success"}), 200
    else:
        return jsonify({"status": "wrong_password"}), 200

#-------------------------------------------
@app.route("/save_product_price", methods=["POST"])
def save_product_price():
    try:
        data = request.get_json()
        print("📦 JSON RECEIVED:", data)

        if not data:
            return jsonify({
                "status": "error",
                "message": "No JSON data"
            }), 400

        # ===============================
        # 1️⃣ รับค่าหลัก (รองรับหลายรูปแบบชื่อ)
        # ===============================
        shopname = data.get("shopname") or data.get("Shopname")
        textmode = data.get("textmode") or data.get("Textmode")
        productname = data.get("productname")
        image_url = data.get("image_url", "")

        if not shopname or not textmode or not productname:
            return jsonify({
                "status": "error",
                "message": "Missing shopname, textmode, or productname"
            }), 400

        # ===============================
        # 2️⃣ รับค่าตัวเลข (กันพัง)
        # ===============================
        try:
            num_remainpack = int(data.get("num_remainpack", 0))
        except:
            num_remainpack = 0

        try:
            numpack = int(data.get("numpack", 0))
        except:
            numpack = 0

        unitproduct = data.get("unitproduct", "")

        try:
            pricepack = float(data.get("pricepack", 0))
        except:
            pricepack = 0.0

        # ✅ รองรับ pricesingle + priceSingle
        try:
            pricesingle = float(
                data.get("pricesingle") or
                data.get("priceSingle") or
                0
            )
        except:
            pricesingle = 0.0

        # ===============================
        # 3️⃣ บันทึก Firestore
        # ===============================
        db.collection("Shopname") \
          .document(shopname) \
          .collection("mode") \
          .document(textmode) \
          .collection("product") \
          .document(productname) \
          .set({
              
              "num_remainpack": num_remainpack,
              "numpack": numpack,
              "unitproduct": unitproduct,
              "pricepack": pricepack,
              "pricesingle": pricesingle,
              "productname":productname,
              "image_url": image_url
          }, merge=True)

        return jsonify({
            "status": "success",
            "message": "✅ บันทึกข้อมูลเรียบร้อย"
        }), 200

    except Exception as e:
        print("🔥 ERROR:", e)
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500
#-------------------- mode from storage --------------------------------
@app.route("/get_modes", methods=["GET"])
def get_modes():
    shopname = request.args.get("shopname")
    if not shopname:
        return jsonify([])

    bucket = storage.bucket()
    blobs = bucket.list_blobs(prefix=f"{shopname}/", delimiter="/")

    modes = []
    for page in blobs.pages:
        if page.prefixes:
            for p in page.prefixes:
                mode = p.replace(f"{shopname}/", "").replace("/", "")
                modes.append(mode)

    return jsonify(modes)
#-----------------------------------------------------
@app.route("/get_products", methods=["GET"])
def get_products():
    shopname = request.args.get("shopname")
    mode = request.args.get("mode")

    if not shopname or not mode:
        return jsonify({"error": "missing params"}), 400

    docs = db.collection("Shopname") \
        .document(shopname) \
        .collection("mode") \
        .document(mode) \
        .collection("product") \
        .stream()

    result = []
    for d in docs:
        data = d.to_dict()
        data["productname"] = d.id
        result.append(data)

    return jsonify(result)
    #---------------------------------------
@app.route("/get_products_by_mode", methods=["GET"])
def get_products_by_mode():
    try:
        shopname = request.args.get("shopname")
        textmode = request.args.get("textmode")

        if not shopname or not textmode:
            return jsonify({
                "status": "error",
                "message": "Missing shopname or textmode"
            }), 400

        products_ref = db.collection("Shopname") \
            .document(shopname) \
            .collection("mode") \
            .document(textmode) \
            .collection("product")

        docs = products_ref.stream()

        products = []
        for doc in docs:
            data = doc.to_dict()
            products.append({
                
                "productname": doc.id,
                
                "num_remainpack": data.get("num_remainpack", 0),
                "pricesingle": data.get("pricesingle", 0),  
                "numpack": data.get("numpack", 0),   
                "pricepack": data.get("pricepack", 0),  

                
                    "productname": data.get("productname", ""),
                "image_url": data.get("image_url", ""),
                 "unitproduct": data.get("unitproduct", "")
                

                
            })

        return jsonify({
            "status": "success",
            "products": products
        })

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500
#------------------------------------
@app.route("/save_order", methods=["POST"])
def save_order():
    data = request.get_json()

    phone = data["phone"]
    productname = data["productname"]
    timestamp = datetime.utcnow().isoformat()

    doc_ref = (
        db.collection("Order")
          .document(phone)
          .collection(productname)
          .document(timestamp)
    )

    doc_ref.set({
        "productname": productname,
        "numberproduct": data["numberproduct"],
        "image_url": data["image_url"],
        "into_unit": data["into_unit"],
        "priceproduct": data["priceproduct"],
        "created_at": firestore.SERVER_TIMESTAMP
    })

    return jsonify({"status": "success"})
    #------------------------------------------
@app.route("/update_save_order", methods=["POST"])
def update_save_order():
    data = request.get_json()

    phone = data["phone"]
    productname = data["productname"]
    timestamp = data["timestamp"]
    numberproduct = data["numberproduct"]

    doc_ref = db.collection("Order").document(phone).collection(productname).document(timestamp)

    # ใช้ merge=True เพื่อไม่ให้ error และไม่ลบ field อื่น
    doc_ref.set({
        "productname": productname,
        "numberproduct": numberproduct,
        "updated_at": firestore.SERVER_TIMESTAMP
    }, merge=True)

    return jsonify({"status": "success"})

#---------------------------------------
@app.route("/get_orders", methods=["GET"])
def get_orders():
    phone = request.args.get("phone")
    orders = []

    # ดึงทุก collection ของ phone
    docs = db.collection("Order").document(phone).collections()
    for product_col in docs:
        for doc in product_col.stream():
            data = doc.to_dict()
            data["timestamp"] = doc.id  # 🔥 เก็บ document id เป็น timestamp

            # แปลง Firestore Timestamp → ISO string
            created = data.get("created_at")
            if created:
                data["created_at"] = created.isoformat()
            else:
                data["created_at"] = None

            orders.append(data)

    return jsonify({
        "status": "success",
        "orders": orders
    })
#----------------------------------------------
@app.route("/delete_order", methods=["POST"])
def delete_order():
    data = request.get_json()

    phone = data["phone"]
    productname = data["productname"]
    timestamp = data["timestamp"]

    order_doc_ref = db.collection("Order").document(phone)

    # 🔥 1. ลบรายการสินค้า
    product_doc_ref = (
        order_doc_ref
        .collection(productname)
        .document(timestamp)
    )
    product_doc_ref.delete()

    # 🔥 2. ลดค่า Preorder ลง 1 (แต่ไม่ให้ติดลบ)
    order_doc = order_doc_ref.get()
    if order_doc.exists:
        current_preorder = order_doc.to_dict().get("Preorder", 0)
        new_preorder = max(current_preorder - 1, 0)

        order_doc_ref.update({
            "Preorder": new_preorder
        })

    return jsonify({
        "status": "success",
        "message": "Order deleted and preorder updated",
        "Preorder": new_preorder
    })


#---------------------------------------------
@app.route("/get_preorder", methods=["GET"])
def get_preorder():
    phone = request.args.get("phone")

    doc_ref = db.collection("Order").document(phone)
    doc = doc_ref.get()

    if not doc.exists:
        # ถ้ายังไม่เคยมี → สร้างเริ่มต้น
        doc_ref.set({
            "Preorder": 0,
            "confirmorder": False
        })
        return jsonify({
            "status": "success",
            "Preorder": 0
        })

    data = doc.to_dict()
    return jsonify({
        "status": "success",
        "Preorder": data.get("Preorder", 0)
    })
#---------------เพิ่ม Preorder ทีละ 1 (ตอนกด BuyPack / BuyUnit) 
@app.route("/inc_preorder", methods=["POST"])
def inc_preorder():
    data = request.get_json()
    phone = data["phone"]

    doc_ref = db.collection("Order").document(phone)

    doc_ref.update({
        "Preorder": firestore.Increment(1)
    })

    return jsonify({"status": "success"})
#----------------------------------------------


@app.route("/register_customer", methods=["POST"])
def register_customer():
    try:
        data = request.get_json()

        shopname = data.get("shopname")
        customer_name = data.get("customerName")
        phone = data.get("phoneNumber")
        address = data.get("address")
        password = data.get("password")

        if not shopname or not customer_name or not phone or not address or not password:
            return jsonify({"status": "error", "message": "Missing required fields"}), 400

        if len(phone) != 10:
            return jsonify({"status": "error", "message": "Phone number must be 10 digits"}), 400

        customer_ref = (
            db.collection("customers")
              .document(customer_name)
 
        )

        if customer_ref.get().exists:
            return jsonify({"status": "error", "message": "Customer already exists"}), 409

        hashed_password = generate_password_hash(password)

        customer_ref.set({
            
            "shopname": shopname,
            "customerName": customer_name,
            "phoneNumber": phone,
            "address": address,
            "passwordHash": hashed_password,
            "createdAt": datetime.utcnow()
        })

        return jsonify({"status": "success", "message": "Customer registered"}), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
        #-------------------------------------------------
@app.route("/get_customer", methods=["POST"])
def get_customer():
    try:
        data = request.get_json()
        customer_name = data.get("customerName")

        if not customer_name:
            return jsonify({
                "status": "error",
                "message": "Missing customerName"
            }), 400

        customer_ref = (
            db.collection("customers")
              .document(customer_name)
        )

        doc = customer_ref.get()
        if not doc.exists:
            return jsonify({
                "status": "error",
                "message": "Customer not found"
            }), 404

        customer = doc.to_dict()

        return jsonify({
            "status": "success",
            "customerName": customer["customerName"],
            "phoneNumber": customer["phoneNumber"],
            "address": customer["address"],
            "shopname": customer["shopname"]
        }), 200

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

#--------------------------------------------
@app.route("/login_customer", methods=["POST"])
def login_customer():
    try:
        data = request.get_json()

        customer_name = data.get("customer_name")
        password = data.get("password")

        if not customer_name or not password:
            return jsonify({
                "status": "error",
                "message": "Missing required fields"
            }), 400

        customer_ref = (
            db.collection("customers")
              .document(customer_name)
        )

        doc = customer_ref.get()
        if not doc.exists:
            return jsonify({
                "status": "error",
                "message": "Customer not found"
            }), 404

        customer = doc.to_dict()

        # ✅ เช็ครหัสผ่าน
        if not check_password_hash(customer["passwordHash"], password):
            return jsonify({
                "status": "error",
                "message": "Invalid password"
            }), 401

        # ✅ return shopname เพิ่ม
        return jsonify({
            "status": "success",
            "message": "Login successful",
            "customerName": customer.get("customerName"),
            "shopname": customer.get("shopname")
        }), 200

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500
#--------------------- สร้าง  qrcode--


@app.route("/generate_qr", methods=["POST"])
def generate_qr():
    try:
        data = request.get_json()

        if not data:
            return jsonify({
                "status": "error",
                "message": "No JSON data"
            }), 400

        tambon = data.get("tambon")
        mode = data.get("mode")        # agent / retail
        ref_store = data.get("ref_store")

        if not tambon or not mode or not ref_store:
            return jsonify({
                "status": "error",
                "message": "Missing required fields"
            }), 400

        # 🔗 URL ที่จะฝังใน QR
        qr_url = (
            f"{INSTALL_URL}"
            f"?tambon={tambon}"
            f"&mode={mode}"
            f"&ref={ref_store}"
        )

        # 🧩 สร้าง QR Code
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(qr_url)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")

        # 🧵 แปลงเป็น Base64
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)

        qr_base64 = base64.b64encode(buffer.read()).decode("utf-8")

        return jsonify({
            "status": "success",
            "qr_url": qr_url,
            "qr_base64": qr_base64
        })

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500