from flask import Flask, render_template, request, redirect, flash as flask_flash, session
from pymongo import MongoClient
from bson import ObjectId
from bson.decimal128 import Decimal128
from passlib.hash import sha256_crypt
from dotenv import load_dotenv
import os
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

load_dotenv()
app = Flask(__name__)
app.config["MONGO_URI"] = os.getenv("MONGOURI", "mongodb://localhost:27017/restaurant")
app.config["SECRET_KEY"] = os.getenv("SECRETKEY") or os.urandom(32)
client = MongoClient(app.config["MONGO_URI"])
db = client.restaurant
try:
    db.Products.create_index([("name", "text"), ("description", "text")])
except Exception as error:
    print(f"Index setup error: {error}")

def get_cart():
    if "user_id" not in session:
        return []
    try:
        cart = db.Carts.find_one({"_id": ObjectId(session["user_id"])})
    except Exception:
        cart = None
    if cart is None:
        session.pop("user_id", None)
        return []
    return cart.get("cart", [])

def ensure_cart_for_new_item():
    if "user_id" not in session:
        session["user_id"] = str(db.Carts.insert_one({"cart": []}).inserted_id)
    return session["user_id"]

def get_cart_count():
    return len(get_cart())

def normalize_price(value):
    if value is None:
        return 0.00
    if hasattr(value, "to_decimal"):
        return float(value.to_decimal().quantize(Decimal("0.01"), rounding = ROUND_HALF_UP))
    try:
        return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding = ROUND_HALF_UP))
    except (TypeError, ValueError, InvalidOperation):
        return 0.00

def normalize_cart_prices(cart):
    normalized = []
    for item in cart:
        item_copy = dict(item)
        item_copy["price"] = normalize_price(item_copy.get("price"))
        normalized.append(item_copy)
    return normalized

def normalize_products(products):
    normalized = []
    for product in products:
        product_copy = dict(product)
        product_copy["price"] = normalize_price(product_copy.get("price"))
        normalized.append(product_copy)
    return normalized

def safe_int(value, default = 0):
    try:
        number = int(value)
        return number if number > 0 else default
    except (TypeError, ValueError):
        return default

def parse_price_decimal128(value):
    try:
        decimal_value = Decimal(str(value)).quantize(Decimal("0.01"), rounding = ROUND_HALF_UP)
        decimal128_value = Decimal128(decimal_value)
        if decimal128_value.to_decimal() < Decimal("0"):
            return None
        return decimal128_value
    except (TypeError, ValueError, InvalidOperation):
        return None

@app.route("/", methods = ["GET", "POST"])
def index():
    search_query = request.args.get("search", "").strip()
    shop_filter = request.args.get("shop", "").strip()
    query = {}
    if search_query:
        query["$or"] = [
            {"name": {"$regex": re.escape(search_query), "$options": "i"}},
            {"description": {"$regex": re.escape(search_query), "$options": "i"}},
        ]
    if shop_filter:
        query["email"] = shop_filter
    try:
        products = normalize_products(list(db.Products.find(query).sort("name", 1)))
        shops = list(db.Shops.find().sort("shop_name", 1))
        shop_names = {shop["email"]: shop["shop_name"] for shop in shops}
        return render_template(
            "index.html",
            shops = shops,
            products = products,
            shop_names = shop_names,
            cart_count = get_cart_count(),
            search_query = search_query,
            shop_filter = shop_filter,
        )
    except Exception as error:
        print(f"Search error: {error}")
        return render_template(
            "index.html",
            shops = list(db.Shops.find().sort("shop_name", 1)),
            products = normalize_products(list(db.Products.find().sort("name", 1))),
            shop_names = {},
            cart_count = get_cart_count(),
            search_query = search_query,
            shop_filter = shop_filter,
        )

@app.route("/register", methods = ["GET", "POST"])
def register():
    if request.method == "POST" and request.form.get("form_id") == "register_form":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        owner_name = request.form.get("owner_name", "").strip()
        shop_name = request.form.get("shop_name", "").strip()
        contact = request.form.get("contact", "").strip()
        if not all([email, password, owner_name, shop_name, contact]):
            flash("Please complete all shop registration fields.")
            return redirect("/")
        if db.Shops.find_one({"email": email}):
            flash("That email is already registered.")
            return redirect("/")
        if db.Shops.find_one({"shop_name": shop_name}):
            flash("That shop name is already registered.")
            return redirect("/")
        try:
            db.Shops.insert_one({
                "email": email,
                "password": sha256_crypt.hash(password),
                "owner_name": owner_name,
                "shop_name": shop_name,
                "contact": contact,
            })
            flash("Shop registered successfully.")
        except Exception as error:
            flash("An error occurred during registration. Please try again.")
            print(f"Registration error: {error}")
        return redirect("/")
    return redirect("/")

@app.route("/owner_shop", methods = ["GET", "POST"])
def owner_shop():
    if "email" not in session:
        flash("You must first login.")
        return redirect("/")
    products = db.Products.find({"email": session["email"]}).sort("name", 1)
    return render_template("owner_shop.html", products = normalize_products(list(products)), name = session["name"])

@app.route("/shop_profile", methods = ["GET", "POST"])
def shop_profile():
    if "email" not in session:
        flash("You must first login.")
        return redirect("/")
    shop = db.Shops.find_one({"email": session["email"]})
    if not shop:
        session.clear()
        flash("Your shop account could not be found.")
        return redirect("/")
    if request.method == "POST":
        owner_name = request.form.get("owner_name", "").strip()
        shop_name = request.form.get("shop_name", "").strip()
        contact = request.form.get("contact", "").strip()
        if not all([owner_name, shop_name, contact]):
            flash("Please complete all shop profile fields.")
            return render_template("shop_profile.html", shop = shop)
        duplicate = db.Shops.find_one({"shop_name": shop_name, "email": {"$ne": session["email"]}})
        if duplicate:
            flash("That shop name is already registered.")
            return render_template("shop_profile.html", shop = shop)
        db.Shops.update_one(
            {"email": session["email"]},
            {"$set": {"owner_name": owner_name, "shop_name": shop_name, "contact": contact}},
        )
        session["name"] = owner_name
        flash("Shop profile updated successfully.")
        return redirect("/owner_shop")
    return render_template("shop_profile.html", shop = shop)

@app.route("/login", methods = ["GET", "POST"])
def login():
    if request.method == "POST" and request.form.get("form_id") == "login_form":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        if not email or not password:
            flash("Email and password are required.")
            return redirect("/")
        try:
            shop = db.Shops.find_one({"email": email})
            if not shop:
                flash("Email is not registered to any shop.")
                return redirect("/")
            if sha256_crypt.verify(password, shop["password"]):
                session["email"] = shop["email"]
                session["name"] = shop["owner_name"]
                flash("Login successful.")
                return redirect("/owner_shop")
            flash("Password is incorrect.")
        except Exception as error:
            flash("An error occurred during login. Please try again.")
            print(f"Login error: {error}")
        return redirect("/")
    return redirect("/")

@app.route("/logout", methods = ["GET", "POST"])
def logout():
    session.clear()
    flash("Logout successful.")
    return redirect("/")

@app.route("/add_product", methods = ["POST"])
def add_product():
    if "email" not in session:
        flash("Please login before adding menu items.")
        return redirect("/")
    if request.form.get("form_id") == "add_product_form":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        price = request.form.get("price", "").strip()
        quantity = safe_int(request.form.get("quantity"), 0)
        image_link = request.form.get("link", "").strip()
        if not all([name, description, image_link]):
            flash("Please provide a menu item name, description, and image link.")
            return redirect("/owner_shop")
        price_value = parse_price_decimal128(price)
        if price_value is None:
            flash("Price must be a positive number.")
            return redirect("/owner_shop")
        if quantity <= 0:
            flash("Quantity must be at least 1.")
            return redirect("/owner_shop")
        if len(name) > 100:
            flash("Menu item name is too long (max 100 characters).")
            return redirect("/owner_shop")
        try:
            db.Products.insert_one({
                "name": name,
                "description": description,
                "price": price_value,
                "quantity": quantity,
                "link": image_link,
                "email": session["email"],
            })
            flash("Menu item added successfully.")
        except Exception as error:
            print(f"Add menu item error: {error}")
            flash("Error adding menu item. Please try again.")
        return redirect("/owner_shop")
    return redirect("/owner_shop")

@app.route("/edit_product/<id>", methods = ["GET", "POST"])
def edit_product(id):
    if "email" not in session:
        flash("Please login before editing menu items.")
        return redirect("/")
    try:
        product = db.Products.find_one({"_id": ObjectId(id), "email": session["email"]})
    except Exception:
        product = None
    if not product:
        flash("Menu item not found or you do not own it.")
        return redirect("/owner_shop")
    product["price"] = normalize_price(product.get("price"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        price = request.form.get("price", "").strip()
        image_link = request.form.get("link", "").strip()
        if not all([name, description, image_link]):
            flash("Please provide a valid menu item name, description, and image URL.")
            return render_template("edit_product.html", product = product)
        price_value = parse_price_decimal128(price)
        if price_value is None:
            flash("Price must be a positive number.")
            return render_template("edit_product.html", product = product)
        if len(name) > 100:
            flash("Menu item name is too long (max 100 characters).")
            return render_template("edit_product.html", product = product)
        db.Products.update_one(
            {"_id": product["_id"], "email": session["email"]},
            {"$set": {"name": name, "description": description, "price": price_value, "link": image_link}},
        )
        flash("Menu item updated successfully.")
        return redirect("/owner_shop")
    return render_template("edit_product.html", product = product)

@app.route("/add_stock/<id>", methods = ["POST"])
def add_stock(id):
    if "email" not in session:
        flash("You must be logged in as a shop owner.")
        return redirect("/")
    try:
        product = db.Products.find_one({"_id": ObjectId(id)})
        if not product:
            flash("Menu item not found.")
            return redirect("/owner_shop")
        if product["email"] != session["email"]:
            flash("You can only manage your own menu items.")
            return redirect("/owner_shop")
        quantity = safe_int(request.form.get("quantity"), 0)
        if quantity <= 0:
            flash("Please enter a valid quantity to add.")
            return redirect("/owner_shop")
        db.Products.update_one({"_id": ObjectId(id)}, {"$inc": {"quantity": quantity}})
        flash(f"Successfully added {quantity} {product['name']}(s) to your menu stock.")
    except Exception as error:
        print(f"Add stock error: {error}")
        flash("Error adding stock.")
    return redirect("/owner_shop")

@app.route("/delete/<id>", methods = ["POST"])
def delete(id):
    if "email" not in session:
        flash("Please login to manage your menu items.")
        return redirect("/")
    try:
        product = db.Products.find_one({"_id": ObjectId(id)})
        if not product:
            flash("Menu item not found.")
            return redirect("/owner_shop")
        if product["email"] != session["email"]:
            flash("You can only delete your own menu items.")
            return redirect("/owner_shop")
        db.Products.delete_one({"_id": ObjectId(id)})
        flash("Menu item deleted successfully.")
    except Exception as error:
        print(f"Delete error: {error}")
        flash("Error deleting product.")
    return redirect("/owner_shop")

@app.route("/view_shop/<email>", methods = ["GET"])
@app.route("/shop/<email>", methods = ["GET"])
def view_shop(email):
    shop = db.Shops.find_one({"email": email})
    if not shop:
        flash("That shop could not be found.")
        return redirect("/")
    products = db.Products.find({"email": email}).sort("name", 1)
    return render_template(
        "customer_shop.html",
        products = normalize_products(list(products)),
        cart_count = get_cart_count(),
        email = email,
        shop = shop,
    )

def add_to_cart(product_id, redirect_target):
    quantity = safe_int(request.form.get("quantity"), 0)
    if quantity <= 0:
        flash("Please enter a valid quantity.")
        return redirect(redirect_target)
    try:
        object_id = ObjectId(product_id)
        product = db.Products.find_one({"_id": object_id})
        if not product:
            flash("This item is no longer available.")
            return redirect(redirect_target)
        stock_update = db.Products.update_one(
            {"_id": object_id, "quantity": {"$gte": quantity}},
            {"$inc": {"quantity": -quantity}},
        )
        if stock_update.modified_count != 1:
            flash(f"Only {product['quantity']} {product['name']}(s) are available in stock.")
            return redirect(redirect_target)
        cart_id = ensure_cart_for_new_item()
        cart = get_cart()
        for item in cart:
            if item["product_id"] == str(product["_id"]):
                item["quantity"] += quantity
                break
        else:
            cart.append({
                "product_id": str(product["_id"]),
                "name": product["name"],
                "quantity": quantity,
                "price": product["price"],
            })
        db.Carts.update_one({"_id": ObjectId(cart_id)}, {"$set": {"cart": cart}})
        flash(f"Successfully added {quantity} {product['name']}(s) to your cart.")
    except Exception as error:
        print(f"Add to cart error: {error}")
        flash("Error adding item to cart.")
    return redirect(redirect_target)

@app.route("/add_cart_home/<id>", methods = ["POST"])
def add_cart_home(id):
    return add_to_cart(id, "/")

@app.route("/add_cart_shop/<id>/<email>", methods = ["POST"])
def add_cart_shop(id, email):
    return add_to_cart(id, f"/shop/{email}")

@app.route("/remove_from_cart/<product_id>", methods = ["POST"])
def remove_from_cart(product_id):
    if "user_id" not in session:
        flash("Your session has expired.")
        return redirect("/")
    try:
        cart = get_cart()
        removed = next((item for item in cart if item["product_id"] == product_id), None)
        if not removed:
            flash("That cart item could not be found.")
            return redirect("/view_cart")
        cart[:] = [item for item in cart if item["product_id"] != product_id]
        db.Products.update_one({"_id": ObjectId(product_id)}, {"$inc": {"quantity": removed["quantity"]}})
        db.Carts.update_one({"_id": ObjectId(session["user_id"])}, {"$set": {"cart": cart}})
        flash(f"Removed {removed['name']} from cart.")
    except Exception as error:
        print(f"Remove from cart error: {error}")
        flash("Error removing item from cart.")
    return redirect("/view_cart")

@app.route("/update_cart_quantity", methods = ["POST"])
def update_cart_quantity():
    if "user_id" not in session:
        flash("Your session has expired.")
        return redirect("/")
    try:
        product_id = request.form.get("product_id", "").strip()
        new_quantity = safe_int(request.form.get("quantity"), 0)
        if not product_id or new_quantity <= 0:
            flash("Invalid quantity.")
            return redirect("/view_cart")
        cart = get_cart()
        for item in cart:
            if item["product_id"] == product_id:
                old_quantity = item["quantity"]
                difference = new_quantity - old_quantity
                if difference > 0:
                    stock_update = db.Products.update_one(
                        {"_id": ObjectId(product_id), "quantity": {"$gte": difference}},
                        {"$inc": {"quantity": -difference}},
                    )
                    if stock_update.modified_count != 1:
                        flash("There is not enough stock for that quantity.")
                        return redirect("/view_cart")
                elif difference < 0:
                    db.Products.update_one({"_id": ObjectId(product_id)}, {"$inc": {"quantity": -difference}})
                item["quantity"] = new_quantity
                break
        db.Carts.update_one({"_id": ObjectId(session["user_id"])}, {"$set": {"cart": cart}})
        flash("Cart updated successfully.")
    except Exception as error:
        print(f"Update cart error: {error}")
        flash("Error updating cart.")
    return redirect("/view_cart")

@app.route("/view_cart", methods = ["GET", "POST"])
def view_cart():
    if "user_id" in session:
        cart = get_cart()
        if not cart:
            db.Carts.delete_one({"_id": ObjectId(session["user_id"])})
            session.pop("user_id", None)
    cart = normalize_cart_prices(get_cart())
    total = sum(item["quantity"] * item["price"] for item in cart)
    return render_template("checkout.html", cart = cart, total = total)

@app.route("/checkout", methods = ["POST"])
def checkout():
    try:
        if "user_id" in session:
            cart = get_cart()
            if not cart:
                db.Carts.delete_one({"_id": ObjectId(session["user_id"])})
                session.pop("user_id", None)
                flash("Your cart is empty.")
                return redirect("/view_cart")
            session.pop("user_id", None)
            flash("Thank you for shopping at Restaurant! Your order has been placed.")
    except Exception as error:
        print(f"Checkout error: {error}")
        flash("An error occurred during checkout. Please try again.")
    return redirect("/")

def flash(message, category = None):
    if category is None:
        text = str(message).lower()
        if any(word in text for word in (
            "error", "failed", "invalid", "not found", "cannot", "not registered",
            "required", "must", "already", "not available", "please", "incorrect",
        )):
            category = "danger"
        elif any(word in text for word in (
            "success", "successfully", "registered", "added", "deleted", "updated",
            "placed", "thank you",
        )):
            category = "success"
        else:
            category = "neutral"
    flask_flash(message, category)

# if __name__ == "__main__":
#     app.run(debug = True)