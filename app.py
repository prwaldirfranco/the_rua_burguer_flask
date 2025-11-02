from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
from datetime import datetime
import json
import sqlite3
import win32print

app = Flask(__name__)
CORS(app)

# =========================
# CONFIGURAÇÃO
# =========================
PRINTER_NAME = "POS-80"  # MUDE PARA O NOME EXATO DA SUA IMPRESSORA
DB_NAME = 'the_rua_burger.db'

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

# =========================
# INICIALIZA BANCO DE DADOS
# =========================
def init_db():
    with app.app_context():
        db = get_db()
        db.executescript('''
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                category TEXT DEFAULT 'Outros',
                options TEXT DEFAULT '[]',
                extras TEXT DEFAULT '[]',
                created_at TEXT,
                ingredients TEXT DEFAULT '[]'  -- ADICIONADO: ingredientes removíveis
            );
            CREATE TABLE IF NOT EXISTS extras (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cash_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                opened_at TEXT,
                closed_at TEXT,
                opening_amount REAL DEFAULT 0,
                closing_amount REAL,
                total_sales REAL DEFAULT 0,
                expected_amount REAL DEFAULT 0,
                difference REAL DEFAULT 0,
                is_open INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_name TEXT DEFAULT 'Cliente',
                type TEXT NOT NULL,
                address TEXT,
                phone TEXT,
                note TEXT,
                total REAL NOT NULL,
                status TEXT DEFAULT 'preparing',
                created_at TEXT,
                payment_method TEXT DEFAULT 'dinheiro',
                cash_session_id INTEGER,
                FOREIGN KEY (cash_session_id) REFERENCES cash_sessions (id)
            );
            CREATE TABLE IF NOT EXISTS order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER,
                product_id INTEGER,
                product_name TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                total REAL NOT NULL,
                extras TEXT DEFAULT '[]',
                removed_ingredients TEXT DEFAULT '[]',
                note TEXT,
                FOREIGN KEY (order_id) REFERENCES orders (id)
            );
        ''')
        # ADICIONA COLUNAS SE NÃO EXISTIREM
        try: db.execute('ALTER TABLE orders ADD COLUMN payment_method TEXT DEFAULT "dinheiro"')
        except: pass
        try: db.execute('ALTER TABLE orders ADD COLUMN cash_session_id INTEGER')
        except: pass
        try: db.execute('ALTER TABLE order_items ADD COLUMN product_id INTEGER')
        except: pass
        try: db.execute('ALTER TABLE products ADD COLUMN ingredients TEXT DEFAULT "[]"')
        except: pass  # ADICIONADO: garante coluna ingredients
        db.commit()

init_db()

# =========================
# FUNÇÃO: GERAR COMANDA ESC/POS
# =========================
def generate_escpos_raw(order):
    ESC = b'\x1b'
    GS = b'\x1d'
    INIT = ESC + b'@'
    CENTER = ESC + b'a' + b'\x01'
    LEFT = ESC + b'a' + b'\x00'
    BOLD_ON = ESC + b'E' + b'\x01'
    BOLD_OFF = ESC + b'E' + b'\x00'
    
    # 🔸 Tamanhos
    SIZE_NORMAL = GS + b'!' + b'\x00'   # normal
    SIZE_MEDIUM = GS + b'!' + b'\x01'   # altura 2x (largura normal)
    SIZE_BIG = GS + b'!' + b'\x11'      # 2x largura e altura (apenas para título e total)
    
    CUT = GS + b'V' + b'\x00'
    LF = b'\n'

    # Cabeçalho
    receipt = INIT + CENTER + SIZE_BIG + BOLD_ON
    receipt += b"THE RUA BURGUER\n"
    receipt += SIZE_MEDIUM + b"COMANDA DE PEDIDO\n\n" + LEFT + BOLD_OFF

    # Informações do pedido
    receipt += SIZE_MEDIUM
    receipt += f"Pedido: #{order['id']}\n".encode('cp1252', errors='replace')
    receipt += f"Cliente: {order.get('customer_name', 'N/A')}\n".encode('cp1252', errors='replace')
    receipt += f"Tipo: {order['type'].upper()}\n".encode('cp1252', errors='replace')
    
    if order.get('address'):
        receipt += f"Endereço: {order['address']}\n".encode('cp1252', errors='replace')
    if order.get('phone'):
        receipt += f"Tel: {order['phone']}\n".encode('cp1252', errors='replace')

    receipt += f"Data: {order['created_at'][:16].replace('T', ' ')}\n".encode('cp1252', errors='replace')
    receipt += b"-" * 42 + LF

    # Itens do pedido
    for item in order['items']:
        name = (item['product_name'][:25] + ' ').encode('cp1252', errors='replace')
        qty = str(item['quantity']).encode()
        price = f"{item['total']:6.2f}".encode()
        receipt += name + b' ' + qty + b'x ' + price + LF

        if item.get('extras'):
            for e in item['extras']:
                receipt += b"  + " + e['name'].encode('cp1252', errors='replace') + LF
        if item.get('removed_ingredients'):
            receipt += b"  Sem: " + ', '.join(item['removed_ingredients']).encode('cp1252', errors='replace') + LF
        if item.get('note'):
            receipt += b"  Obs: " + item['note'].encode('cp1252', errors='replace') + LF

    # Total
    receipt += b"-" * 42 + LF + CENTER + SIZE_BIG + BOLD_ON
    receipt += f"TOTAL: R$ {order['total']:6.2f}\n".encode('cp1252', errors='replace')
    receipt += SIZE_MEDIUM + BOLD_OFF + LF * 2 + CUT

    return receipt


def print_via_windows(receipt_bytes):
    try:
        hPrinter = win32print.OpenPrinter(PRINTER_NAME)
        hJob = win32print.StartDocPrinter(hPrinter, 1, ("Comanda", None, "RAW"))
        win32print.StartPagePrinter(hPrinter)
        win32print.WritePrinter(hPrinter, receipt_bytes)
        win32print.EndPagePrinter(hPrinter)
        win32print.EndDocPrinter(hPrinter)
        win32print.ClosePrinter(hPrinter)
        print("COMANDA IMPRESSA COM SUCESSO!")
    except Exception as e:
        print(f"ERRO NA IMPRESSÃO: {e}")
        raise

def print_report(text):
    ESC = b'\x1b'
    INIT = ESC + b'@'
    CENTER = ESC + b'a' + b'\x01'
    LEFT = ESC + b'a' + b'\x00'
    DOUBLE = b'\x1d' + b'!' + b'\x11'
    NORMAL = b'\x1d' + b'!' + b'\x00'
    CUT = b'\x1d' + b'V' + b'\x00'
    LF = b'\n'

    lines = text.split('\n')
    receipt = INIT + CENTER + DOUBLE + b"FECHAMENTO DE CAIXA\n" + NORMAL + LF
    for line in lines:
        if line.strip():
            receipt += line.encode('cp1252', errors='replace') + LF
    receipt += b"\n\n" + CUT

    try:
        print_via_windows(receipt)
        print("RELATÓRIO DE FECHAMENTO IMPRESSO!")
    except Exception as e:
        print(f"ERRO AO IMPRIMIR RELATÓRIO: {e}")

# =========================
# ROTAS DE PÁGINAS
# =========================
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/pdv')
def pdv():
    return render_template('pdv.html')

@app.route('/kitchen')
def kitchen():
    return render_template('kitchen.html')

@app.route('/products')
def products_page():
    return render_template('products.html')

@app.route('/extras')
def extras_page():
    return render_template('extras.html')

@app.route('/cashier')
def cashier():
    return render_template('cashier.html')

@app.route('/delivery')
def delivery():
    return render_template('delivery.html')

@app.route('/orders')
def orders_page():
    return render_template('orders.html')

# =========================
# API: PRODUTOS (AGORA SALVA INGREDIENTS)
# =========================
@app.route('/api/products', methods=['GET'])
def get_products():
    db = get_db()
    products = db.execute("SELECT * FROM products").fetchall()
    return jsonify({'success': True, 'products': [dict(p) for p in products]})

@app.route('/api/products', methods=['POST'])
def add_product():
    data = request.json
    name = data.get('name')
    price = data.get('price')
    category = data.get('category', 'Outros')
    options = json.dumps(data.get('options', []))
    extras_list = json.dumps(data.get('extras', []))
    ingredients = json.dumps(data.get('ingredients', []))  # ADICIONADO

    if not name or price is None:
        return jsonify({'success': False, 'message': 'Nome e preço obrigatórios!'}), 400

    try:
        price = float(price)
        if price < 0:
            return jsonify({'success': False, 'message': 'Preço inválido.'}), 400
    except:
        return jsonify({'success': False, 'message': 'Preço inválido.'}), 400

    db = get_db()
    cursor = db.execute(
        "INSERT INTO products (name, price, category, options, extras, created_at, ingredients) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, price, category, options, extras_list, datetime.now().isoformat(), ingredients)
    )
    db.commit()
    return jsonify({'success': True, 'product': {'id': cursor.lastrowid, 'name': name, 'price': price}})

@app.route('/api/products/<int:product_id>', methods=['DELETE'])
def delete_product(product_id):
    db = get_db()
    cursor = db.execute("DELETE FROM products WHERE id = ?", (product_id,))
    db.commit()
    return jsonify({'success': cursor.rowcount > 0})

@app.route('/api/products/<int:product_id>', methods=['PUT'])
def update_product(product_id):
    data = request.json
    db = get_db()
    product = db.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not product:
        return jsonify({'success': False, 'message': 'Produto não encontrado.'}), 404

    name = data.get('name', product['name'])
    try:
        price = float(data.get('price', product['price']))
        if price < 0: raise ValueError()
    except:
        return jsonify({'success': False, 'message': 'Preço inválido.'}), 400

    db.execute(
        "UPDATE products SET name=?, price=?, category=?, options=?, extras=?, ingredients=? WHERE id=?",
        (name, price, data.get('category', product['category']),
         json.dumps(data.get('options', json.loads(product['options']))),
         json.dumps(data.get('extras', json.loads(product['extras']))),
         json.dumps(data.get('ingredients', json.loads(product['ingredients']))), product_id)
    )
    db.commit()
    return jsonify({'success': True})

# =========================
# API: EXTRAS
# =========================
@app.route('/api/extras', methods=['GET'])
def get_extras():
    db = get_db()
    extras = db.execute("SELECT * FROM extras").fetchall()
    return jsonify({'success': True, 'extras': [dict(e) for e in extras]})

@app.route('/api/extras', methods=['POST'])
def add_extra():
    data = request.json
    name = data.get('name')
    price = data.get('price')
    if not name or price is None:
        return jsonify({'success': False, 'message': 'Nome e preço obrigatórios!'}), 400
    try:
        price = float(price)
        if price < 0: raise ValueError()
    except:
        return jsonify({'success': False, 'message': 'Preço inválido.'}), 400

    db = get_db()
    cursor = db.execute("INSERT INTO extras (name, price) VALUES (?, ?)", (name, price))
    db.commit()
    return jsonify({'success': True, 'extra': {'id': cursor.lastrowid, 'name': name, 'price': price}})

@app.route('/api/extras/<int:extra_id>', methods=['DELETE'])
def delete_extra(extra_id):
    db = get_db()
    cursor = db.execute("DELETE FROM extras WHERE id = ?", (extra_id,))
    db.commit()
    return jsonify({'success': cursor.rowcount > 0})

# =========================
# API: CAIXA
# =========================
@app.route('/api/cash/open', methods=['POST'])
def open_cash():
    amount = float(request.json.get('opening_amount', 0))
    if amount < 0:
        return jsonify({'success': False, 'message': 'Valor inválido.'}), 400

    db = get_db()
    if db.execute("SELECT 1 FROM cash_sessions WHERE is_open = 1").fetchone():
        return jsonify({'success': False, 'message': 'Caixa já aberto!'}), 400

    cursor = db.execute("INSERT INTO cash_sessions (opened_at, opening_amount, is_open) VALUES (?, ?, 1)",
                        (datetime.now().isoformat(), amount))
    db.commit()
    return jsonify({'success': True, 'cash': {'id': cursor.lastrowid}})

@app.route('/api/cash/status')
def cash_status():
    db = get_db()
    cash = db.execute("SELECT * FROM cash_sessions WHERE is_open = 1").fetchone()
    return jsonify({'success': True, 'is_open': bool(cash), 'cash': dict(cash) if cash else None})

@app.route('/api/cash/report')
def get_cash_report():
    try:
        db = get_db()
        cash = db.execute("SELECT * FROM cash_sessions WHERE is_open = 1").fetchone()
        if not cash:
            return jsonify({'success': False, 'message': 'Caixa fechado'}), 400

        payments = db.execute("""
            SELECT COALESCE(payment_method, 'dinheiro') as method, SUM(total) as total
            FROM orders 
            WHERE cash_session_id = ? 
            GROUP BY COALESCE(payment_method, 'dinheiro')
        """, (cash['id'],)).fetchall()

        breakdown = {'dinheiro': 0.0, 'pix': 0.0, 'cartao': 0.0}
        total_sales = 0.0
        for p in payments:
            method = p['method'].lower()
            if method in breakdown:
                breakdown[method] = float(p['total'])
                total_sales += float(p['total'])

        expected = cash['opening_amount'] + total_sales

        return jsonify({
            'success': True,
            'cash': {'id': cash['id'], 'opened_at': cash['opened_at']},
            'report': {
                'total_sales': total_sales,
                'expected': expected,
                'opening_amount': float(cash['opening_amount']),
                'payment_breakdown': breakdown
            }
        })

    except Exception as e:
        print(f"[ERRO] /api/cash/report: {e}")
        return jsonify({'success': False, 'message': 'Erro interno'}), 500

@app.route('/api/cash/close', methods=['POST'])
def close_cash_session():
    data = request.get_json() or {}
    closing_amount = float(data.get('closing_amount', 0))

    try:
        db = get_db()
        cash = db.execute("SELECT * FROM cash_sessions WHERE is_open = 1").fetchone()
        if not cash:
            return jsonify({'success': False, 'message': 'Nenhum caixa aberto'}), 400

        payments = db.execute("""
            SELECT COALESCE(payment_method, 'dinheiro') as method, SUM(total) as total
            FROM orders WHERE cash_session_id = ? 
            GROUP BY COALESCE(payment_method, 'dinheiro')
        """, (cash['id'],)).fetchall()

        breakdown = {'dinheiro': 0.0, 'pix': 0.0, 'cartao': 0.0}
        total_sales = 0.0
        for p in payments:
            method = p['method'].lower()
            if method in breakdown:
                breakdown[method] = float(p['total'])
                total_sales += float(p['total'])

        expected = cash['opening_amount'] + total_sales
        difference = closing_amount - expected

        db.execute("""
            UPDATE cash_sessions 
            SET is_open = 0, closed_at = CURRENT_TIMESTAMP, closing_amount = ?, 
                total_sales = ?, expected_amount = ?, difference = ?
            WHERE id = ?
        """, (closing_amount, total_sales, expected, difference, cash['id']))
        db.commit()

        report_lines = [
            "================================",
            "     FECHAMENTO DE CAIXA        ",
            "================================",
            f"ID do Caixa: {cash['id']}",
            f"Abertura:    {cash['opened_at'][:19].replace('T', ' ')}",
            f"Fechamento:  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "----- VENDAS POR PAGAMENTO -----",
            f"Dinheiro:    R$ {breakdown['dinheiro']:.2f}",
            f"PIX:         R$ {breakdown['pix']:.2f}",
            f"Cartão:      R$ {breakdown['cartao']:.2f}",
            "",
            "----- RESUMO DO CAIXA -----",
            f"Valor inicial:   R$ {cash['opening_amount']:.2f}",
            f"Total em vendas: R$ {total_sales:.2f}",
            f"Esperado:        R$ {expected:.2f}",
            f"Em caixa:        R$ {closing_amount:.2f}",
            f"Diferença:       R$ {difference:.2f} " + 
            ("[SOBRA]" if difference > 0 else "[FALTA]" if difference < 0 else "[OK]"),
            "",
            "Assinatura: ____________________",
            "",
            "THE RUA BURGUER - SISTEMA PDV",
            "================================"
        ]

        print_report("\n".join(report_lines))

        return jsonify({'success': True, 'difference': difference})

    except Exception as e:
        print(f"[ERRO] /api/cash/close: {e}")
        return jsonify({'success': False, 'message': 'Erro ao fechar caixa'}), 500

# =========================
# API: PEDIDOS (CORRIGIDO: SALVA REMOVED_INGREDIENTS)
# =========================
@app.route('/api/orders/new', methods=['POST'])
def create_order():
    data = request.json
    total = float(data.get('total', 0))
    if total <= 0 or not data.get('items'):
        return jsonify({'success': False, 'message': 'Dados inválidos'}), 400

    db = get_db()
    cash = db.execute("SELECT id FROM cash_sessions WHERE is_open = 1").fetchone()
    if not cash:
        return jsonify({'success': False, 'message': 'Caixa não aberto'}), 400

    payment_method = data.get('payment_method', 'dinheiro').lower()
    if payment_method not in ['dinheiro', 'pix', 'cartao']:
        payment_method = 'dinheiro'

    cursor = db.execute("""
        INSERT INTO orders (customer_name, type, address, phone, note, total, status, created_at, payment_method, cash_session_id)
        VALUES (?, ?, ?, ?, ?, ?, 'preparing', ?, ?, ?)
    """, (
        data.get('customer_name', 'Cliente'),
        data.get('type', 'local'),
        data.get('address'),
        data.get('phone'),
        data.get('note'),
        total,
        datetime.now().isoformat(),
        payment_method,
        cash['id']
    ))
    order_id = cursor.lastrowid

    for item in data['items']:
        # === GARANTE LISTAS VÁLIDAS ===
        extras = item.get('extras', [])
        if not isinstance(extras, list):
            extras = []

        removed_ingredients = item.get('removed_ingredients', [])
        if not isinstance(removed_ingredients, list):
            removed_ingredients = []

        # === SALVA NO BANCO COM JSON SEGURO ===
        db.execute("""
            INSERT INTO order_items 
            (order_id, product_id, product_name, quantity, total, extras, removed_ingredients, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            order_id,
            item.get('product_id'),
            item.get('product_name', 'Item'),
            int(item.get('quantity', 1)),
            float(item.get('total', 0)),
            json.dumps(extras),
            json.dumps(removed_ingredients),
            item.get('note', '') or ''
        ))
    db.commit()
    print(f"PEDIDO #{order_id} CRIADO -> COZINHA | Pagamento: {payment_method.upper()}")
    return jsonify({'success': True, 'order': {'id': order_id}})

@app.route('/api/orders', methods=['GET'])
def get_orders():
    db = get_db()
    orders = db.execute("SELECT * FROM orders WHERE status IN ('preparing', 'ready') ORDER BY created_at DESC").fetchall()
    result = []
    for o in orders:
        items = db.execute("SELECT * FROM order_items WHERE order_id = ?", (o['id'],)).fetchall()
        result.append({
            'id': o['id'],
            'customer_name': o['customer_name'],
            'type': o['type'],
            'address': o['address'],
            'phone': o['phone'],
            'note': o['note'],
            'total': float(o['total']),
            'status': o['status'],
            'created_at': o['created_at'],
            'items': [{
                'product_name': i['product_name'],
                'quantity': i['quantity'],
                'total': i['total'],
                'extras': json.loads(i['extras']) if i['extras'] and i['extras'] != 'null' else [],
                'removed_ingredients': json.loads(i['removed_ingredients']) if i['removed_ingredients'] and i['removed_ingredients'] != 'null' else [],
                'note': i['note']
            } for i in items]
        })
    return jsonify({'orders': result})

@app.route('/api/orders/<int:order_id>/ready', methods=['POST'])
def mark_ready(order_id):
    db = get_db()
    if not db.execute("SELECT 1 FROM orders WHERE id = ?", (order_id,)).fetchone():
        return jsonify({'success': False, 'message': 'Pedido não encontrado'}), 404
    db.execute("UPDATE orders SET status = 'ready' WHERE id = ?", (order_id,))
    db.commit()
    print(f"PEDIDO #{order_id} MARCADO COMO PRONTO")
    return jsonify({'success': True})

@app.route('/api/orders/<int:order_id>/complete', methods=['POST'])
def complete_order(order_id):
    db = get_db()
    order = db.execute("SELECT 1 FROM orders WHERE id = ? AND status = 'ready'", (order_id,)).fetchone()
    if not order:
        return jsonify({'success': False, 'message': 'Pedido não pronto ou não encontrado'}), 404
    db.execute("UPDATE orders SET status = 'completed' WHERE id = ?", (order_id,))
    db.commit()
    print(f"PEDIDO #{order_id} FINALIZADO")
    return jsonify({'success': True})

# =========================
# API: IMPRESSÃO (CORRIGIDO: MOSTRA REMOVED_INGREDIENTS)
# =========================
@app.route('/api/print/order', methods=['POST'])
def print_order():
    order_id = request.json.get('order_id')
    if not order_id:
        return jsonify({'success': False, 'message': 'order_id obrigatório'}), 400

    db = get_db()
    order = db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        return jsonify({'success': False, 'message': 'Pedido não encontrado'}), 404

    items = db.execute("SELECT * FROM order_items WHERE order_id = ?", (order_id,)).fetchall()
    order_dict = dict(order)
    order_dict['items'] = [{
        'product_name': i['product_name'],
        'quantity': i['quantity'],
        'total': i['total'],
        'extras': json.loads(i['extras']) if i['extras'] and i['extras'] != 'null' else [],
        'removed_ingredients': json.loads(i['removed_ingredients']) if i['removed_ingredients'] and i['removed_ingredients'] != 'null' else [],
        'note': i['note']
    } for i in items]

    try:
        print_via_windows(generate_escpos_raw(order_dict))
        return jsonify({'success': True, 'message': 'Comanda impressa!'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# =========================
# API: ENTREGAS
# =========================
@app.route('/api/deliveries')
def get_deliveries():
    try:
        db = get_db()
        orders = db.execute("""
            SELECT o.id, o.customer_name, o.address, o.phone, o.total, o.status, o.created_at,
                   GROUP_CONCAT(oi.product_name || ' x' || oi.quantity) as items
            FROM orders o
            LEFT JOIN order_items oi ON oi.order_id = o.id
            WHERE o.type = 'entrega' AND o.status IN ('ready', 'delivering')
            GROUP BY o.id
            ORDER BY o.created_at ASC
        """).fetchall()

        deliveries = []
        for o in orders:
            deliveries.append({
                'id': o['id'],
                'customer_name': o['customer_name'],
                'address': o['address'] or 'Sem endereço',
                'phone': o['phone'] or 'Sem telefone',
                'total': float(o['total']),
                'status': 'pronto para entrega' if o['status'] == 'ready' else 'em entrega',
                'created_at': o['created_at'],
                'items': [item.strip() for item in (o['items'] or '').split(',') if item.strip()]
            })

        return jsonify({'success': True, 'deliveries': deliveries})
    except Exception as e:
        print(f"[ERRO] /api/deliveries: {e}")
        return jsonify({'success': False, 'message': 'Erro interno'}), 500

@app.route('/api/deliveries/<int:order_id>/delivered', methods=['POST'])
def mark_delivered(order_id):
    try:
        db = get_db()
        order = db.execute("SELECT 1 FROM orders WHERE id = ? AND type = 'entrega' AND status IN ('ready', 'delivering')", (order_id,)).fetchone()
        if not order:
            return jsonify({'success': False, 'message': 'Pedido não encontrado ou já finalizado'}), 404

        db.execute("UPDATE orders SET status = 'delivered' WHERE id = ?", (order_id,))
        db.commit()

        print(f"PEDIDO #{order_id} MARCADO COMO ENTREGUE")
        return jsonify({'success': True})
    except Exception as e:
        print(f"[ERRO] /api/deliveries/{order_id}/delivered: {e}")
        return jsonify({'success': False, 'message': 'Erro interno'}), 500

# =========================
# INICIAR SERVIDOR
# =========================
if __name__ == '__main__':
    print("="*60)
    print("THE RUA BURGUER - SISTEMA COMPLETO")
    print("="*60)
    print(f"IMPRESSORA: {PRINTER_NAME}")
    print("ACESSE:")
    print("  MENU: http://127.0.0.1:5000/")
    print("  PDV:  http://127.0.0.1:5000/pdv")
    print("  COZINHA: http://127.0.0.1:5000/kitchen")
    print("  ENTREGADOR: http://127.0.0.1:5000/delivery")
    print("="*60)
    app.run(host='0.0.0.0', port=5000, debug=True)