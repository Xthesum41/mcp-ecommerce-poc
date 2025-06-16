import asyncio
import json
import sys
import re
import logging
import os
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import (
    Resource,
    Tool,
    TextContent,
    ImageContent,
    EmbeddedResource,
    LoggingLevel
)
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, PyMongoError
from bson import ObjectId

# Configure logging
logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)
logger = logging.getLogger(__name__)

@dataclass
class UserData:
    name: str
    email: str = ""
    phone: str = ""
    age: Optional[int] = None
    _id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}

@dataclass
class ProductData:
    name: str
    description: str
    price: float
    category: str
    piece_type: str
    color: str
    size: str
    collection: str
    stock_quantity: int
    brand: str = ""
    _id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}

@dataclass
class PurchaseData:
    user_id: str
    product_id: str
    quantity: int
    total_price: float
    purchase_date: datetime
    user_name: str = ""        # ← ADICIONADO
    user_email: str = ""       # ← ADICIONADO
    product_name: str = ""     # ← ADICIONADO (já existia no código)
    product_price: float = 0.0 # ← ADICIONADO (já existia no código)
    _id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}

class UserValidator:
    EMAIL_PATTERN = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
    
    @classmethod
    def is_valid_email(cls, email: str) -> bool:
        return bool(cls.EMAIL_PATTERN.match(email))
    
    @classmethod
    def validate_user_data(cls, data: Dict[str, Any]) -> List[str]:
        errors = []
        
        if not data.get('name'):
            errors.append("Nome é obrigatório")
        
        email = data.get('email')
        if email and not cls.is_valid_email(email):
            errors.append("Email inválido")
            
        return errors

class ProductValidator:
    CATEGORIES = ["Casual", "Formal", "Esportivo", "Praia", "Inverno", "Festa"]
    PIECE_TYPES = ["Camiseta", "Calça", "Vestido", "Saia", "Blusa", "Jaqueta", "Shorts", "Casaco", "Sapato", "Acessório"]
    COLORS = ["Preto", "Branco", "Azul", "Vermelho", "Verde", "Amarelo", "Rosa", "Roxo", "Marrom", "Cinza", "Bege", "Laranja"]
    SIZES = ["PP", "P", "M", "G", "GG", "XGG", "34", "36", "38", "40", "42", "44", "46", "48"]
    
    @classmethod
    def validate_product_data(cls, data: Dict[str, Any]) -> List[str]:
        errors = []
        
        if not data.get('name'):
            errors.append("Nome do produto é obrigatório")
        
        if not data.get('price') or data.get('price') <= 0:
            errors.append("Preço deve ser maior que zero")
            
        if data.get('category') and data['category'] not in cls.CATEGORIES:
            errors.append(f"Categoria deve ser uma das: {', '.join(cls.CATEGORIES)}")
            
        if data.get('piece_type') and data['piece_type'] not in cls.PIECE_TYPES:
            errors.append(f"Tipo de peça deve ser um dos: {', '.join(cls.PIECE_TYPES)}")
            
        if data.get('color') and data['color'] not in cls.COLORS:
            errors.append(f"Cor deve ser uma das: {', '.join(cls.COLORS)}")
            
        if data.get('size') and data['size'] not in cls.SIZES:
            errors.append(f"Tamanho deve ser um dos: {', '.join(cls.SIZES)}")
            
        if data.get('stock_quantity') is not None and data['stock_quantity'] < 0:
            errors.append("Quantidade em estoque não pode ser negativa")
            
        return errors

class DatabaseManager:
    def __init__(self, connection_string: str = 'mongodb://localhost:27017/'):
        self.connection_string = connection_string
        self.client = None
        self.db = None
        self.users_collection = None
        self.products_collection = None
        self.purchases_collection = None
        self._connect()
    
    def _connect(self):
        try:
            self.client = MongoClient(self.connection_string, serverSelectionTimeoutMS=5000)
            self.client.admin.command('ping')  # Test connection
            self.db = self.client['store_management']
            self.users_collection = self.db['users']
            self.products_collection = self.db['products']
            self.purchases_collection = self.db['purchases']
            logger.info("MongoDB conectado com sucesso")
        except (ConnectionFailure, PyMongoError) as e:
            logger.error(f"Erro ao conectar MongoDB: {e}")
            self.users_collection = None
            self.products_collection = None
            self.purchases_collection = None
    
    def is_connected(self) -> bool:
        # Correção: verificar se as coleções não são None ao invés de usar truth testing
        return (self.users_collection is not None and 
                self.products_collection is not None and 
                self.purchases_collection is not None)
    
    def serialize_document(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        if doc and '_id' in doc:
            doc['_id'] = str(doc['_id'])
        return doc

    def serialize_user(self, user: Dict[str, Any]) -> Dict[str, Any]:
        return self.serialize_document(user)

class UserService:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.validator = UserValidator()
    
    async def create_user(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        # Verificar se a conexão está disponível
        if self.db.users_collection is None:
            raise ValueError("Conexão com banco de dados não disponível")
            
        # Validate data
        errors = self.validator.validate_user_data(user_data)
        if errors:
            raise ValueError("; ".join(errors))
        
        # Check email uniqueness
        email = user_data.get('email')
        if email and self.db.users_collection.find_one({'email': email}):
            raise ValueError("Email já cadastrado")
        
        # Create user document
        now = datetime.utcnow()
        user_doc = {
            'name': user_data['name'],
            'email': email or '',
            'phone': user_data.get('phone', ''),
            'age': user_data.get('age'),
            'created_at': now,
            'updated_at': now
        }
        
        result = self.db.users_collection.insert_one(user_doc)
        user_doc['_id'] = str(result.inserted_id)
        
        return self.db.serialize_user(user_doc)
    
    async def get_users(self, query: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        if self.db.users_collection is None:
            return []
            
        query = query or {}
        users = list(self.db.users_collection.find(query))
        return [self.db.serialize_user(user) for user in users]
    
    async def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        if self.db.users_collection is None:
            raise ValueError("Conexão com banco de dados não disponível")
            
        if not ObjectId.is_valid(user_id):
            raise ValueError("ID inválido")
        
        user = self.db.users_collection.find_one({'_id': ObjectId(user_id)})
        return self.db.serialize_user(user) if user else None
    
    async def update_user(self, user_id: str, update_data: Dict[str, Any]) -> Dict[str, Any]:
        if not ObjectId.is_valid(user_id):
            raise ValueError("ID inválido")
        
        user = self.db.users_collection.find_one({'_id': ObjectId(user_id)})
        if not user:
            raise ValueError("Usuário não encontrado")
        
        # Validate email if provided
        if 'email' in update_data:
            email = update_data['email']
            if email and not self.validator.is_valid_email(email):
                raise ValueError("Email inválido")
            
            if email and self.db.users_collection.find_one({
                'email': email,
                '_id': {'$ne': ObjectId(user_id)}
            }):
                raise ValueError("Email já cadastrado para outro usuário")
        
        # Update user
        allowed_fields = ['name', 'email', 'phone', 'age']
        filtered_data = {k: v for k, v in update_data.items() if k in allowed_fields}
        
        if filtered_data:
            filtered_data['updated_at'] = datetime.utcnow()
            self.db.users_collection.update_one(
                {'_id': ObjectId(user_id)},
                {'$set': filtered_data}
            )
        
        updated_user = self.db.users_collection.find_one({'_id': ObjectId(user_id)})
        return self.db.serialize_user(updated_user)
    
    async def delete_user(self, user_id: str) -> bool:
        if not ObjectId.is_valid(user_id):
            raise ValueError("ID inválido")
        
        result = self.db.users_collection.delete_one({'_id': ObjectId(user_id)})
        if result.deleted_count == 0:
            raise ValueError("Usuário não encontrado")
        
        return True

class ProductService:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.validator = ProductValidator()
    
    async def create_product(self, product_data: Dict[str, Any]) -> Dict[str, Any]:
        # Verificar se a conexão está disponível
        if self.db.products_collection is None:
            raise ValueError("Conexão com banco de dados não disponível")
            
        errors = self.validator.validate_product_data(product_data)
        if errors:
            raise ValueError("; ".join(errors))
        
        now = datetime.utcnow()
        product_doc = {
            'name': product_data['name'],
            'description': product_data.get('description', ''),
            'price': float(product_data['price']),
            'category': product_data.get('category', 'Casual'),
            'piece_type': product_data.get('piece_type', 'Camiseta'),
            'color': product_data.get('color', 'Branco'),
            'size': product_data.get('size', 'M'),
            'collection': product_data.get('collection', 'Básica'),
            'stock_quantity': product_data.get('stock_quantity', 0),
            'brand': product_data.get('brand', ''),
            'created_at': now,
            'updated_at': now
        }
        
        result = self.db.products_collection.insert_one(product_doc)
        product_doc['_id'] = str(result.inserted_id)
        
        return self.db.serialize_document(product_doc)
    
    async def get_products(self, query: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        if self.db.products_collection is None:
            return []
            
        query = query or {}
        products = list(self.db.products_collection.find(query))
        return [self.db.serialize_document(product) for product in products]
    
    async def get_product_by_id(self, product_id: str) -> Optional[Dict[str, Any]]:
        if self.db.products_collection is None:
            raise ValueError("Conexão com banco de dados não disponível")
            
        if not ObjectId.is_valid(product_id):
            raise ValueError("ID inválido")
        
        product = self.db.products_collection.find_one({'_id': ObjectId(product_id)})
        return self.db.serialize_document(product) if product else None
    
    async def update_product(self, product_id: str, update_data: Dict[str, Any]) -> Dict[str, Any]:
        if not ObjectId.is_valid(product_id):
            raise ValueError("ID inválido")
        
        product = self.db.products_collection.find_one({'_id': ObjectId(product_id)})
        if not product:
            raise ValueError("Produto não encontrado")
        
        errors = self.validator.validate_product_data(update_data)
        if errors:
            raise ValueError("; ".join(errors))
        
        allowed_fields = ['name', 'description', 'price', 'category', 'piece_type', 'color', 'size', 'collection', 'stock_quantity', 'brand']
        filtered_data = {k: v for k, v in update_data.items() if k in allowed_fields}
        
        if filtered_data:
            filtered_data['updated_at'] = datetime.utcnow()
            self.db.products_collection.update_one(
                {'_id': ObjectId(product_id)},
                {'$set': filtered_data}
            )
        
        updated_product = self.db.products_collection.find_one({'_id': ObjectId(product_id)})
        return self.db.serialize_document(updated_product)
    
    async def delete_product(self, product_id: str) -> bool:
        if not ObjectId.is_valid(product_id):
            raise ValueError("ID inválido")
        
        result = self.db.products_collection.delete_one({'_id': ObjectId(product_id)})
        if result.deleted_count == 0:
            raise ValueError("Produto não encontrado")
        
        return True
    
    async def search_products(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        query = {}
        
        if filters.get('name'):
            query['name'] = {'$regex': filters['name'], '$options': 'i'}
        if filters.get('category'):
            query['category'] = filters['category']
        if filters.get('piece_type'):
            query['piece_type'] = filters['piece_type']
        if filters.get('color'):
            query['color'] = filters['color']
        if filters.get('size'):
            query['size'] = filters['size']
        if filters.get('collection'):
            query['collection'] = {'$regex': filters['collection'], '$options': 'i'}
        if filters.get('brand'):
            query['brand'] = {'$regex': filters['brand'], '$options': 'i'}
        
        # Price range
        price_filter = {}
        if filters.get('price_min'):
            price_filter['$gte'] = float(filters['price_min'])
        if filters.get('price_max'):
            price_filter['$lte'] = float(filters['price_max'])
        if price_filter:
            query['price'] = price_filter
        
        # Stock availability
        if filters.get('in_stock'):
            query['stock_quantity'] = {'$gt': 0}
        
        products = list(self.db.products_collection.find(query))
        return [self.db.serialize_document(product) for product in products]

class PurchaseService:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
    
    async def create_purchase(self, purchase_data: Dict[str, Any]) -> Dict[str, Any]:
        # Verificar se as conexões estão disponíveis
        if (self.db.users_collection is None or 
            self.db.products_collection is None or 
            self.db.purchases_collection is None):
            raise ValueError("Conexão com banco de dados não disponível")
            
        user_id = purchase_data.get('user_id')
        product_id = purchase_data.get('product_id')
        quantity = purchase_data.get('quantity', 1)
        
        if not ObjectId.is_valid(user_id):
            raise ValueError("ID do usuário inválido")
        if not ObjectId.is_valid(product_id):
            raise ValueError("ID do produto inválido")
        if quantity <= 0:
            raise ValueError("Quantidade deve ser maior que zero")
        
        # Check if user exists
        user = self.db.users_collection.find_one({'_id': ObjectId(user_id)})
        if not user:
            raise ValueError("Usuário não encontrado")
        
        # Check if product exists and has stock
        product = self.db.products_collection.find_one({'_id': ObjectId(product_id)})
        if not product:
            raise ValueError("Produto não encontrado")
        if product['stock_quantity'] < quantity:
            raise ValueError("Estoque insuficiente")
        
        # Calculate total price
        total_price = product['price'] * quantity
        
        # Create purchase record with user name
        purchase_doc = {
            'user_id': user_id,
            'user_name': user['name'],           # ← ADICIONADO: Nome do usuário
            'user_email': user.get('email', ''), # ← ADICIONADO: Email do usuário (opcional)
            'product_id': product_id,
            'product_name': product['name'],
            'product_price': product['price'],
            'quantity': quantity,
            'total_price': total_price,
            'purchase_date': datetime.utcnow()
        }
        
        result = self.db.purchases_collection.insert_one(purchase_doc)
        purchase_doc['_id'] = str(result.inserted_id)
        
        # Update product stock
        self.db.products_collection.update_one(
            {'_id': ObjectId(product_id)},
            {'$inc': {'stock_quantity': -quantity}}
        )
        
        return self.db.serialize_document(purchase_doc)
    
    async def get_user_purchases(self, user_id: str) -> List[Dict[str, Any]]:
        if not ObjectId.is_valid(user_id):
            raise ValueError("ID do usuário inválido")
        
        purchases = list(self.db.purchases_collection.find({'user_id': user_id}).sort('purchase_date', -1))
        return [self.db.serialize_document(purchase) for purchase in purchases]
    
    async def get_purchase_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        purchases = list(self.db.purchases_collection.find().sort('purchase_date', -1).limit(limit))
        return [self.db.serialize_document(purchase) for purchase in purchases]

class RecommendationService:
    def __init__(self, db_manager: DatabaseManager, product_service: ProductService, purchase_service: PurchaseService):
        self.db = db_manager
        self.product_service = product_service
        self.purchase_service = purchase_service
    
    async def get_recommendations_for_user(self, user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        if not ObjectId.is_valid(user_id):
            raise ValueError("ID do usuário inválido")
        
        # Get user's purchase history
        user_purchases = await self.purchase_service.get_user_purchases(user_id)
        
        if not user_purchases:
            # If no purchase history, return popular items
            return await self._get_popular_products(limit)
        
        # Analyze user preferences
        preferences = self._analyze_user_preferences(user_purchases)
        
        # Get recommendations based on preferences
        recommendations = await self._get_recommendations_by_preferences(preferences, user_purchases, limit)
        
        return recommendations
    
    def _analyze_user_preferences(self, purchases: List[Dict[str, Any]]) -> Dict[str, Any]:
        categories = {}
        piece_types = {}
        colors = {}
        price_range = []
        
        for purchase in purchases:
            # Get product details for each purchase
            product = self.db.products_collection.find_one({'_id': ObjectId(purchase['product_id'])})
            if product:
                # Count categories
                category = product.get('category', '')
                categories[category] = categories.get(category, 0) + purchase['quantity']
                
                # Count piece types
                piece_type = product.get('piece_type', '')
                piece_types[piece_type] = piece_types.get(piece_type, 0) + purchase['quantity']
                
                # Count colors
                color = product.get('color', '')
                colors[color] = colors.get(color, 0) + purchase['quantity']
                
                # Track price range
                price_range.append(product['price'])
        
        # Get most frequent preferences
        preferred_category = max(categories, key=categories.get) if categories else None
        preferred_piece_type = max(piece_types, key=piece_types.get) if piece_types else None
        preferred_color = max(colors, key=colors.get) if colors else None
        
        avg_price = sum(price_range) / len(price_range) if price_range else 0
        
        return {
            'preferred_category': preferred_category,
            'preferred_piece_type': preferred_piece_type,
            'preferred_color': preferred_color,
            'average_price': avg_price,
            'categories': categories,
            'piece_types': piece_types,
            'colors': colors
        }
    
    async def _get_recommendations_by_preferences(self, preferences: Dict[str, Any], 
                                                user_purchases: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
        # Get IDs of already purchased products
        purchased_product_ids = [purchase['product_id'] for purchase in user_purchases]
        
        recommendations = []
        
        # Strategy 1: Same category, different piece type
        if preferences['preferred_category']:
            query = {
                'category': preferences['preferred_category'],
                '_id': {'$nin': [ObjectId(pid) for pid in purchased_product_ids]},
                'stock_quantity': {'$gt': 0}
            }
            products = list(self.db.products_collection.find(query).limit(limit // 3))
            recommendations.extend([self.db.serialize_document(p) for p in products])
        
        # Strategy 2: Same piece type, different category
        if preferences['preferred_piece_type'] and len(recommendations) < limit:
            remaining = limit - len(recommendations)
            query = {
                'piece_type': preferences['preferred_piece_type'],
                '_id': {'$nin': [ObjectId(pid) for pid in purchased_product_ids]},
                'stock_quantity': {'$gt': 0}
            }
            products = list(self.db.products_collection.find(query).limit(remaining // 2))
            recommendations.extend([self.db.serialize_document(p) for p in products])
        
        # Strategy 3: Similar price range
        if preferences['average_price'] > 0 and len(recommendations) < limit:
            remaining = limit - len(recommendations)
            price_tolerance = preferences['average_price'] * 0.3  # 30% tolerance
            query = {
                'price': {
                    '$gte': preferences['average_price'] - price_tolerance,
                    '$lte': preferences['average_price'] + price_tolerance
                },
                '_id': {'$nin': [ObjectId(pid) for pid in purchased_product_ids]},
                'stock_quantity': {'$gt': 0}
            }
            products = list(self.db.products_collection.find(query).limit(remaining))
            recommendations.extend([self.db.serialize_document(p) for p in products])
        
        # Remove duplicates and add recommendation scores
        unique_recommendations = []
        seen_ids = set()
        
        for product in recommendations:
            if product['_id'] not in seen_ids:
                product['recommendation_score'] = self._calculate_recommendation_score(product, preferences)
                unique_recommendations.append(product)
                seen_ids.add(product['_id'])
        
        # Sort by recommendation score
        unique_recommendations.sort(key=lambda x: x.get('recommendation_score', 0), reverse=True)
        
        return unique_recommendations[:limit]
    
    def _calculate_recommendation_score(self, product: Dict[str, Any], preferences: Dict[str, Any]) -> float:
        score = 0.0
        
        # Category match
        if product.get('category') == preferences.get('preferred_category'):
            score += 3.0
        
        # Piece type match
        if product.get('piece_type') == preferences.get('preferred_piece_type'):
            score += 2.0
        
        # Color match
        if product.get('color') == preferences.get('preferred_color'):
            score += 1.5
        
        # Price similarity
        if preferences.get('average_price', 0) > 0:
            price_diff = abs(product.get('price', 0) - preferences['average_price'])
            price_score = max(0, 1 - (price_diff / preferences['average_price']))
            score += price_score
        
        return score
    
    async def _get_popular_products(self, limit: int) -> List[Dict[str, Any]]:
        # Get products with most purchases
        pipeline = [
            {'$group': {
                '_id': '$product_id',
                'total_quantity': {'$sum': '$quantity'},
                'total_sales': {'$sum': 1}
            }},
            {'$sort': {'total_quantity': -1}},
            {'$limit': limit}
        ]
        
        popular_product_ids = list(self.db.purchases_collection.aggregate(pipeline))
        
        if not popular_product_ids:
            # If no purchase history exists, return random products
            products = list(self.db.products_collection.find({'stock_quantity': {'$gt': 0}}).limit(limit))
            return [self.db.serialize_document(p) for p in products]
        
        # Get full product details
        product_ids = [ObjectId(item['_id']) for item in popular_product_ids]
        products = list(self.db.products_collection.find({'_id': {'$in': product_ids}}))
        
        return [self.db.serialize_document(p) for p in products]

class SampleDataGenerator:
    def __init__(self, product_service: ProductService):
        self.product_service = product_service
    
    async def generate_sample_products(self) -> List[Dict[str, Any]]:
        sample_products = [
            # Camisetas Básicas
            {
                'name': 'Camiseta Básica Algodão',
                'description': 'Camiseta 100% algodão, confortável e versátil',
                'price': 29.90,
                'category': 'Casual',
                'piece_type': 'Camiseta',
                'color': 'Branco',
                'size': 'M',
                'collection': 'Básica',
                'stock_quantity': 50,
                'brand': 'BasicWear'
            },
            {
                'name': 'Camiseta Básica Algodão',
                'description': 'Camiseta 100% algodão, confortável e versátil',
                'price': 29.90,
                'category': 'Casual',
                'piece_type': 'Camiseta',
                'color': 'Preto',
                'size': 'M',
                'collection': 'Básica',
                'stock_quantity': 45,
                'brand': 'BasicWear'
            },
            {
                'name': 'Camiseta Básica Algodão',
                'description': 'Camiseta 100% algodão, confortável e versátil',
                'price': 29.90,
                'category': 'Casual',
                'piece_type': 'Camiseta',
                'color': 'Azul',
                'size': 'G',
                'collection': 'Básica',
                'stock_quantity': 40,
                'brand': 'BasicWear'
            },
            
            # Calças Jeans
            {
                'name': 'Calça Jeans Skinny',
                'description': 'Calça jeans skinny com elastano para maior conforto',
                'price': 89.90,
                'category': 'Casual',
                'piece_type': 'Calça',
                'color': 'Azul',
                'size': '38',
                'collection': 'Denim',
                'stock_quantity': 30,
                'brand': 'DenimStyle'
            },
            {
                'name': 'Calça Jeans Reta',
                'description': 'Calça jeans com corte reto, clássica e atemporal',
                'price': 79.90,
                'category': 'Casual',
                'piece_type': 'Calça',
                'color': 'Preto',
                'size': '40',
                'collection': 'Denim',
                'stock_quantity': 25,
                'brand': 'DenimStyle'
            },
            
            # Vestidos
            {
                'name': 'Vestido Floral Verão',
                'description': 'Vestido leve com estampa floral, perfeito para o verão',
                'price': 119.90,
                'category': 'Casual',
                'piece_type': 'Vestido',
                'color': 'Rosa',
                'size': 'P',
                'collection': 'Verão 2024',
                'stock_quantity': 20,
                'brand': 'FloralChic'
            },
            {
                'name': 'Vestido Longo Festa',
                'description': 'Vestido longo elegante para ocasiões especiais',
                'price': 199.90,
                'category': 'Festa',
                'piece_type': 'Vestido',
                'color': 'Preto',
                'size': 'M',
                'collection': 'Elegance',
                'stock_quantity': 15,
                'brand': 'ElegantDress'
            },
            
            # Blusas
            {
                'name': 'Blusa Social Feminina',
                'description': 'Blusa social em tecido nobre, ideal para trabalho',
                'price': 69.90,
                'category': 'Formal',
                'piece_type': 'Blusa',
                'color': 'Branco',
                'size': 'M',
                'collection': 'Office',
                'stock_quantity': 35,
                'brand': 'OfficeLook'
            },
            {
                'name': 'Blusa Casual Manga Longa',
                'description': 'Blusa casual confortável para o dia a dia',
                'price': 49.90,
                'category': 'Casual',
                'piece_type': 'Blusa',
                'color': 'Cinza',
                'size': 'G',
                'collection': 'Confort',
                'stock_quantity': 40,
                'brand': 'ComfortWear'
            },
            
            # Jaquetas
            {
                'name': 'Jaqueta Jeans Clássica',
                'description': 'Jaqueta jeans atemporal, combina com tudo',
                'price': 129.90,
                'category': 'Casual',
                'piece_type': 'Jaqueta',
                'color': 'Azul',
                'size': 'M',
                'collection': 'Denim',
                'stock_quantity': 18,
                'brand': 'DenimStyle'
            },
            {
                'name': 'Casaco de Inverno',
                'description': 'Casaco quente e elegante para o inverno',
                'price': 249.90,
                'category': 'Inverno',
                'piece_type': 'Casaco',
                'color': 'Preto',
                'size': 'G',
                'collection': 'Winter',
                'stock_quantity': 12,
                'brand': 'WinterWarm'
            },
            
            # Shorts
            {
                'name': 'Shorts Jeans Feminino',
                'description': 'Shorts jeans com barra desfiada, tendência atual',
                'price': 59.90,
                'category': 'Casual',
                'piece_type': 'Shorts',
                'color': 'Azul',
                'size': '36',
                'collection': 'Summer',
                'stock_quantity': 28,
                'brand': 'SummerVibes'
            },
            {
                'name': 'Shorts Esportivo',
                'description': 'Shorts para atividades físicas, tecido dry-fit',
                'price': 39.90,
                'category': 'Esportivo',
                'piece_type': 'Shorts',
                'color': 'Preto',
                'size': 'M',
                'collection': 'Sport',
                'stock_quantity': 35,
                'brand': 'ActiveFit'
            }
        ]
        
        created_products = []
        for product_data in sample_products:
            try:
                product = await self.product_service.create_product(product_data)
                created_products.append(product)
            except Exception as e:
                logger.error(f"Erro ao criar produto {product_data['name']}: {e}")
        
        return created_products

class ExportService:
    FIELD_MAPPING = {
        '_id': 'ID',
        'name': 'Nome',
        'email': 'Email',
        'phone': 'Telefone',
        'age': 'Idade',
        'created_at': 'Criado em',
        'updated_at': 'Atualizado em'
    }
    
    def __init__(self, user_service: UserService):
        self.user_service = user_service
    
    def build_query(self, filter_criteria: Dict[str, Any]) -> Dict[str, Any]:
        query = {}
        
        if filter_criteria.get('name'):
            query['name'] = {'$regex': filter_criteria['name'], '$options': 'i'}
        if filter_criteria.get('email'):
            query['email'] = {'$regex': filter_criteria['email'], '$options': 'i'}
        
        age_filter = {}
        if filter_criteria.get('age_min'):
            age_filter['$gte'] = filter_criteria['age_min']
        if filter_criteria.get('age_max'):
            age_filter['$lte'] = filter_criteria['age_max']
        if age_filter:
            query['age'] = age_filter
            
        return query
    
    def filter_user_fields(self, users: List[Dict[str, Any]], selected_fields: List[str]) -> List[Dict[str, Any]]:
        filtered_users = []
        for user in users:
            filtered_user = {}
            for field in selected_fields:
                if field in user:
                    value = user[field]
                    if field in ['created_at', 'updated_at'] and value:
                        filtered_user[field] = value.strftime('%Y-%m-%d %H:%M:%S') if hasattr(value, 'strftime') else str(value)
                    else:
                        filtered_user[field] = value if value is not None else ''
            filtered_users.append(filtered_user)
        return filtered_users
    
    def generate_csv(self, users: List[Dict[str, Any]], selected_fields: List[str]) -> str:
        if not users:
            return ""
        
        headers = [self.FIELD_MAPPING.get(field, field) for field in selected_fields]
        csv_lines = [';'.join(headers)]
        
        for user in users:
            row = []
            for field in selected_fields:
                value = str(user.get(field, ''))
                if ';' in value or '"' in value or '\n' in value or ',' in value:
                    value = f'"{value.replace('"', '""')}"'
                row.append(value)
            csv_lines.append(';'.join(row))
        
        return '\n'.join(csv_lines)
    
    async def export_users_csv(self, filename: str, filter_criteria: Dict[str, Any], 
                              selected_fields: List[str]) -> str:
        # Get filtered users
        query = self.build_query(filter_criteria)
        users = await self.user_service.get_users(query)
        
        if not users:
            return "📋 Nenhum usuário encontrado para exportação"
        
        # Filter fields
        filtered_users = self.filter_user_fields(users, selected_fields)
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        
        csv_content = self.generate_csv(filtered_users, selected_fields)
        return self._save_csv_to_desktop(csv_content, f"{filename}_{timestamp}.csv", filtered_users)
    
    def _save_csv_to_desktop(self, content: str, full_filename: str, 
                            users: List[Dict[str, Any]]) -> str:
        desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
        file_path = os.path.join(desktop_path, full_filename)
        
        try:
            with open(file_path, 'w', encoding='utf-8-sig', newline='') as f:
                f.write(content)
            
            response_parts = [
                f"✅ Arquivo CSV criado com sucesso no Desktop!",
                f"📁 Local: {file_path}",
                f"📊 Total de registros: {len(users)}",
                "",
                "💡 Para abrir o arquivo:",
                f"   - Duplo clique no arquivo no Desktop",
                f"   - Ou comando: start \"{file_path}\"",
                "",
                f"📝 Prévia do conteúdo CSV:",
                f"```csv\n{content[:500]}{'...' if len(content) > 500 else ''}\n```"
            ]
            
            logger.info(f"📊 CSV salvo no Desktop: {file_path}")
            return "\n".join(response_parts)
            
        except Exception as e:
            logger.error(f"❌ Erro ao salvar CSV no Desktop: {e}")
            return f"❌ Erro ao salvar arquivo no Desktop: {str(e)}\n\n📝 Conteúdo CSV gerado:\n```csv\n{content}\n```"

class DashboardService:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
    
    async def generate_dashboard(self) -> Dict[str, Any]:
        """Generate comprehensive business dashboard data"""
        dashboard_data = {
            'overview': await self._get_overview_metrics(),
            'users': await self._get_user_analytics(),
            'products': await self._get_product_analytics(),
            'sales': await self._get_sales_analytics(),
            'recommendations': await self._get_recommendation_metrics(),
            'generated_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
        }
        return dashboard_data
    
    async def _get_overview_metrics(self) -> Dict[str, Any]:
        """Get high-level business metrics"""
        if not self.db.is_connected():
            return {'error': 'Database not connected'}
        
        # Count totals
        total_users = self.db.users_collection.count_documents({})
        total_products = self.db.products_collection.count_documents({})
        total_purchases = self.db.purchases_collection.count_documents({})
        products_in_stock = self.db.products_collection.count_documents({'stock_quantity': {'$gt': 0}})
        
        # Calculate total revenue
        revenue_pipeline = [
            {'$group': {'_id': None, 'total_revenue': {'$sum': '$total_price'}}}
        ]
        revenue_result = list(self.db.purchases_collection.aggregate(revenue_pipeline))
        total_revenue = revenue_result[0]['total_revenue'] if revenue_result else 0
        
        # Average order value
        avg_order_value = total_revenue / total_purchases if total_purchases > 0 else 0
        
        return {
            'total_users': total_users,
            'total_products': total_products,
            'products_in_stock': products_in_stock,
            'products_out_of_stock': total_products - products_in_stock,
            'total_purchases': total_purchases,
            'total_revenue': round(total_revenue, 2),
            'average_order_value': round(avg_order_value, 2)
        }
    
    async def _get_user_analytics(self) -> Dict[str, Any]:
        """Get user-related analytics"""
        if not self.db.is_connected():
            return {'error': 'Database not connected'}
        
        # Users with purchases
        users_with_purchases = len(list(self.db.purchases_collection.distinct('user_id')))
        total_users = self.db.users_collection.count_documents({})
        
        # Age distribution
        age_pipeline = [
            {'$match': {'age': {'$exists': True, '$ne': None}}},
            {'$bucket': {
                'groupBy': '$age',
                'boundaries': [0, 18, 25, 35, 45, 55, 100],
                'default': 'Other',
                'output': {'count': {'$sum': 1}}
            }}
        ]
        age_distribution = list(self.db.users_collection.aggregate(age_pipeline))
        
        # Recent registrations (last 30 days)
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        recent_users = self.db.users_collection.count_documents({
            'created_at': {'$gte': thirty_days_ago}
        })
        
        return {
            'total_users': total_users,
            'active_buyers': users_with_purchases,
            'inactive_users': total_users - users_with_purchases,
            'conversion_rate': round((users_with_purchases / total_users * 100), 2) if total_users > 0 else 0,
            'recent_registrations_30d': recent_users,
            'age_distribution': age_distribution
        }
    
    async def _get_product_analytics(self) -> Dict[str, Any]:
        """Get product-related analytics"""
        if not self.db.is_connected():
            return {'error': 'Database not connected'}
        
        # Category distribution
        category_pipeline = [
            {'$group': {'_id': '$category', 'count': {'$sum': 1}, 'avg_price': {'$avg': '$price'}}}
        ]
        category_stats = list(self.db.products_collection.aggregate(category_pipeline))
        
        # Top selling products
        top_products_pipeline = [
            {'$group': {
                '_id': '$product_id',
                'product_name': {'$first': '$product_name'},
                'total_quantity': {'$sum': '$quantity'},
                'total_revenue': {'$sum': '$total_price'}
            }},
            {'$sort': {'total_quantity': -1}},
            {'$limit': 5}
        ]
        top_products = list(self.db.purchases_collection.aggregate(top_products_pipeline))
        
        # Low stock products
        low_stock_products = list(self.db.products_collection.find(
            {'stock_quantity': {'$lt': 10, '$gt': 0}},
            {'name': 1, 'stock_quantity': 1}
        ).limit(10))
        
        # Price analysis
        price_pipeline = [
            {'$group': {
                '_id': None,
                'avg_price': {'$avg': '$price'},
                'min_price': {'$min': '$price'},
                'max_price': {'$max': '$price'}
            }}
        ]
        price_stats = list(self.db.products_collection.aggregate(price_pipeline))
        price_info = price_stats[0] if price_stats else {}
        
        return {
            'category_distribution': category_stats,
            'top_selling_products': top_products,
            'low_stock_alerts': low_stock_products,
            'price_analysis': {
                'average_price': round(price_info.get('avg_price', 0), 2),
                'min_price': price_info.get('min_price', 0),
                'max_price': price_info.get('max_price', 0)
            }
        }
    
    async def _get_sales_analytics(self) -> Dict[str, Any]:
        """Get sales-related analytics"""
        if not self.db.is_connected():
            return {'error': 'Database not connected'}
        
        # Sales by month (last 6 months)
        six_months_ago = datetime.utcnow() - timedelta(days=180)
        monthly_sales_pipeline = [
            {'$match': {'purchase_date': {'$gte': six_months_ago}}},
            {'$group': {
                '_id': {
                    'year': {'$year': '$purchase_date'},
                    'month': {'$month': '$purchase_date'}
                },
                'total_sales': {'$sum': '$total_price'},
                'total_orders': {'$sum': 1},
                'total_items': {'$sum': '$quantity'}
            }},
            {'$sort': {'_id.year': 1, '_id.month': 1}}
        ]
        monthly_sales = list(self.db.purchases_collection.aggregate(monthly_sales_pipeline))
        
        # Recent sales (last 7 days)
        seven_days_ago = datetime.utcnow() - timedelta(days=7)
        recent_sales = self.db.purchases_collection.count_documents({
            'purchase_date': {'$gte': seven_days_ago}
        })
        
        # Best customers (top 5 by total spent)
        best_customers_pipeline = [
            {'$group': {
                '_id': '$user_id',
                'user_name': {'$first': '$user_name'},
                'total_spent': {'$sum': '$total_price'},
                'total_orders': {'$sum': 1}
            }},
            {'$sort': {'total_spent': -1}},
            {'$limit': 5}
        ]
        best_customers = list(self.db.purchases_collection.aggregate(best_customers_pipeline))
        
        # Sales by category
        category_sales_pipeline = [
            {'$lookup': {
                'from': 'products',
                'localField': 'product_id',
                'foreignField': '_id',
                'as': 'product_info'
            }},
            {'$unwind': '$product_info'},
            {'$group': {
                '_id': '$product_info.category',
                'total_revenue': {'$sum': '$total_price'},
                'total_items_sold': {'$sum': '$quantity'}
            }},
            {'$sort': {'total_revenue': -1}}
        ]
        category_sales = list(self.db.purchases_collection.aggregate(category_sales_pipeline))
        
        return {
            'monthly_sales_trend': monthly_sales,
            'recent_sales_7d': recent_sales,
            'best_customers': best_customers,
            'sales_by_category': category_sales
        }
    
    async def _get_recommendation_metrics(self) -> Dict[str, Any]:
        """Get recommendation system metrics"""
        if not self.db.is_connected():
            return {'error': 'Database not connected'}
        
        # Users with purchase history (eligible for personalized recommendations)
        users_with_history = len(list(self.db.purchases_collection.distinct('user_id')))
        total_users = self.db.users_collection.count_documents({})
        
        # Most popular categories based on purchases
        popular_categories_pipeline = [
            {'$lookup': {
                'from': 'products',
                'localField': 'product_id',
                'foreignField': '_id',
                'as': 'product_info'
            }},
            {'$unwind': '$product_info'},
            {'$group': {
                '_id': '$product_info.category',
                'popularity_score': {'$sum': '$quantity'}
            }},
            {'$sort': {'popularity_score': -1}},
            {'$limit': 5}
        ]
        popular_categories = list(self.db.purchases_collection.aggregate(popular_categories_pipeline))
        
        # Color preferences
        color_preferences_pipeline = [
            {'$lookup': {
                'from': 'products',
                'localField': 'product_id',
                'foreignField': '_id',
                'as': 'product_info'
            }},
            {'$unwind': '$product_info'},
            {'$group': {
                '_id': '$product_info.color',
                'preference_score': {'$sum': '$quantity'}
            }},
            {'$sort': {'preference_score': -1}},
            {'$limit': 5}
        ]
        color_preferences = list(self.db.purchases_collection.aggregate(color_preferences_pipeline))
        
        return {
            'users_eligible_for_recommendations': users_with_history,
            'recommendation_coverage': round((users_with_history / total_users * 100), 2) if total_users > 0 else 0,
            'popular_categories': popular_categories,
            'color_preferences': color_preferences
        }

class MCPUserServer:
    def __init__(self):
        self.db_manager = DatabaseManager()
        self.user_service = UserService(self.db_manager)
        self.product_service = ProductService(self.db_manager)
        self.purchase_service = PurchaseService(self.db_manager)
        self.recommendation_service = RecommendationService(self.db_manager, self.product_service, self.purchase_service)
        self.export_service = ExportService(self.user_service)
        self.sample_data_generator = SampleDataGenerator(self.product_service)
        self.dashboard_service = DashboardService(self.db_manager)
        self.server = Server("store-management")
        self._setup_handlers()
    
    def _setup_handlers(self):
        @self.server.list_tools()
        async def handle_list_tools() -> List[Tool]:
            logger.info("📋 Listando ferramentas...")
            return self._get_tool_definitions()
        
        @self.server.call_tool()
        async def handle_call_tool(name: str, arguments: dict) -> List[TextContent]:
            logger.info(f"🔧 Ferramenta chamada: {name} com argumentos: {arguments}")
            
            if not self.db_manager.is_connected():
                return [TextContent(type="text", text="❌ Erro: MongoDB não está disponível")]
            
            try:
                return await self._handle_tool_call(name, arguments)
            except Exception as e:
                error_msg = f"💥 Erro: {str(e)}"
                logger.error(f"ERRO: {error_msg}")
                return [TextContent(type="text", text=error_msg)]
    
    def _get_tool_definitions(self) -> List[Tool]:
        return [
            # User tools (existing)
            Tool(
                name="create_user",
                description="Criar um novo usuário",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Nome do usuário"},
                        "email": {"type": "string", "description": "Email do usuário"},
                        "phone": {"type": "string", "description": "Telefone do usuário"},
                        "age": {"type": "integer", "description": "Idade do usuário"}
                    },
                    "required": ["name"]
                }
            ),
            Tool(
                name="get_users",
                description="Listar todos os usuários",
                inputSchema={
                    "type": "object",
                    "properties": {}
                }
            ),
            Tool(
                name="get_user_by_id",
                description="Buscar usuário por ID",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string", "description": "ID do usuário"}
                    },
                    "required": ["user_id"]
                }
            ),
            Tool(
                name="update_user",
                description="Atualizar dados de um usuário",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string", "description": "ID do usuário"},
                        "name": {"type": "string", "description": "Nome do usuário"},
                        "email": {"type": "string", "description": "Email do usuário"},
                        "phone": {"type": "string", "description": "Telefone do usuário"},
                        "age": {"type": "integer", "description": "Idade do usuário"}
                    },
                    "required": ["user_id"]
                }
            ),
            Tool(
                name="delete_user",
                description="Deletar um usuário",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string", "description": "ID do usuário"}
                    },
                    "required": ["user_id"]
                }
            ),
            # Product tools (new)
            Tool(
                name="create_product",
                description="Criar um novo produto",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Nome do produto"},
                        "description": {"type": "string", "description": "Descrição do produto"},
                        "price": {"type": "number", "description": "Preço do produto"},
                        "category": {"type": "string", "enum": ProductValidator.CATEGORIES, "description": "Categoria do produto"},
                        "piece_type": {"type": "string", "enum": ProductValidator.PIECE_TYPES, "description": "Tipo de peça"},
                        "color": {"type": "string", "enum": ProductValidator.COLORS, "description": "Cor do produto"},
                        "size": {"type": "string", "enum": ProductValidator.SIZES, "description": "Tamanho do produto"},
                        "collection": {"type": "string", "description": "Coleção do produto"},
                        "stock_quantity": {"type": "integer", "description": "Quantidade em estoque"},
                        "brand": {"type": "string", "description": "Marca do produto"}
                    },
                    "required": ["name", "price"]
                }
            ),
            Tool(
                name="get_products",
                description="Listar todos os produtos",
                inputSchema={
                    "type": "object",
                    "properties": {}
                }
            ),
            Tool(
                name="get_product_by_id",
                description="Buscar produto por ID",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "product_id": {"type": "string", "description": "ID do produto"}
                    },
                    "required": ["product_id"]
                }
            ),
            Tool(
                name="update_product",
                description="Atualizar dados de um produto",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "product_id": {"type": "string", "description": "ID do produto"},
                        "name": {"type": "string", "description": "Nome do produto"},
                        "description": {"type": "string", "description": "Descrição do produto"},
                        "price": {"type": "number", "description": "Preço do produto"},
                        "category": {"type": "string", "enum": ProductValidator.CATEGORIES},
                        "piece_type": {"type": "string", "enum": ProductValidator.PIECE_TYPES},
                        "color": {"type": "string", "enum": ProductValidator.COLORS},
                        "size": {"type": "string", "enum": ProductValidator.SIZES},
                        "collection": {"type": "string", "description": "Coleção do produto"},
                        "stock_quantity": {"type": "integer", "description": "Quantidade em estoque"},
                        "brand": {"type": "string", "description": "Marca do produto"}
                    },
                    "required": ["product_id"]
                }
            ),
            Tool(
                name="delete_product",
                description="Deletar um produto",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "product_id": {"type": "string", "description": "ID do produto"}
                    },
                    "required": ["product_id"]
                }
            ),
            Tool(
                name="search_products",
                description="Buscar produtos com filtros",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Nome do produto (busca parcial)"},
                        "category": {"type": "string", "enum": ProductValidator.CATEGORIES},
                        "piece_type": {"type": "string", "enum": ProductValidator.PIECE_TYPES},
                        "color": {"type": "string", "enum": ProductValidator.COLORS},
                        "size": {"type": "string", "enum": ProductValidator.SIZES},
                        "collection": {"type": "string", "description": "Coleção (busca parcial)"},
                        "brand": {"type": "string", "description": "Marca (busca parcial)"},
                        "price_min": {"type": "number", "description": "Preço mínimo"},
                        "price_max": {"type": "number", "description": "Preço máximo"},
                        "in_stock": {"type": "boolean", "description": "Apenas produtos em estoque"}
                    }
                }
            ),
            # Purchase tools (new)
            Tool(
                name="create_purchase",
                description="Registrar uma nova compra",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string", "description": "ID do usuário"},
                        "product_id": {"type": "string", "description": "ID do produto"},
                        "quantity": {"type": "integer", "description": "Quantidade comprada", "default": 1}
                    },
                    "required": ["user_id", "product_id"]
                }
            ),
            Tool(
                name="get_user_purchases",
                description="Buscar histórico de compras de um usuário",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string", "description": "ID do usuário"}
                    },
                    "required": ["user_id"]
                }
            ),
            Tool(
                name="get_purchase_history",
                description="Buscar histórico geral de compras",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "description": "Limite de resultados", "default": 100}
                    }
                }
            ),
            # Recommendation tools (new)
            Tool(
                name="get_user_recommendations",
                description="Obter recomendações personalizadas para um usuário",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string", "description": "ID do usuário"},
                        "limit": {"type": "integer", "description": "Número de recomendações", "default": 10}
                    },
                    "required": ["user_id"]
                }
            ),
            # Sample data tools (new)
            Tool(
                name="generate_sample_products",
                description="Gerar produtos de exemplo para a loja",
                inputSchema={
                    "type": "object",
                    "properties": {}
                }
            ),
            # Existing tools
            Tool(
                name="batch_create_users",
                description="Criar múltiplos usuários de uma vez",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "users": {
                            "type": "array",
                            "description": "Lista de usuários para criar",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string", "description": "Nome do usuário"},
                                    "email": {"type": "string", "description": "Email do usuário"},
                                    "phone": {"type": "string", "description": "Telefone do usuário"},
                                    "age": {"type": "integer", "description": "Idade do usuário"}
                                },
                                "required": ["name"]
                            }
                        }
                    },
                    "required": ["users"]
                }
            ),
            Tool(
                name="batch_operations",
                description="Realizar múltiplas operações (criar, atualizar, deletar) em uma única chamada",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "operations": {
                            "type": "array",
                            "description": "Lista de operações para executar",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "action": {
                                        "type": "string", 
                                        "enum": ["create", "update", "delete"],
                                        "description": "Tipo de operação"
                                    },
                                    "data": {
                                        "type": "object",
                                        "description": "Dados da operação (varia conforme o tipo)"
                                    }
                                },
                                "required": ["action", "data"]
                            }
                        }
                    },
                    "required": ["operations"]
                }
            ),
            Tool(
                name="export_users",
                description="Exportar usuários em formato CSV criando arquivo físico no Desktop",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "filename": {
                            "type": "string",
                            "description": "Nome do arquivo para exportação (opcional)",
                            "default": "usuarios_export"
                        },
                        "filter": {
                            "type": "object",
                            "description": "Filtros opcionais para exportação",
                            "properties": {
                                "name": {"type": "string", "description": "Filtrar por nome (contém)"},
                                "email": {"type": "string", "description": "Filtrar por email (contém)"},
                                "age_min": {"type": "integer", "description": "Idade mínima"},
                                "age_max": {"type": "integer", "description": "Idade máxima"}
                            }
                        },
                        "fields": {
                            "type": "array",
                            "description": "Campos específicos para exportar",
                            "items": {
                                "type": "string",
                                "enum": ["_id", "name", "email", "phone", "age", "created_at", "updated_at"]
                            }
                        }
                    }
                }
            ),
            # Dashboard tool (new)
            Tool(
                name="generate_dashboard",
                description="Gerar dashboard completo com visão geral do negócio, métricas de usuários, produtos, vendas e recomendações",
                inputSchema={
                    "type": "object",
                    "properties": {}
                }
            )
        ]
    
    async def _handle_tool_call(self, name: str, arguments: dict) -> List[TextContent]:
        handler_map = {
            # User handlers (existing)
            "create_user": self._handle_create_user,
            "get_users": self._handle_get_users,
            "get_user_by_id": self._handle_get_user_by_id,
            "update_user": self._handle_update_user,
            "delete_user": self._handle_delete_user,
            "batch_create_users": self._handle_batch_create_users,
            "batch_operations": self._handle_batch_operations,
            "export_users": self._handle_export_users,
            # Product handlers (new)
            "create_product": self._handle_create_product,
            "get_products": self._handle_get_products,
            "get_product_by_id": self._handle_get_product_by_id,
            "update_product": self._handle_update_product,
            "delete_product": self._handle_delete_product,
            "search_products": self._handle_search_products,
            # Purchase handlers (new)
            "create_purchase": self._handle_create_purchase,
            "get_user_purchases": self._handle_get_user_purchases,
            "get_purchase_history": self._handle_get_purchase_history,
            # Recommendation handlers (new)
            "get_user_recommendations": self._handle_get_user_recommendations,
            # Sample data handlers (new)
            "generate_sample_products": self._handle_generate_sample_products,
            # Dashboard handler (new)
            "generate_dashboard": self._handle_generate_dashboard,
        }
        
        handler = handler_map.get(name)
        if not handler:
            return [TextContent(type="text", text=f"❌ Ferramenta desconhecida: {name}")]
        
        return await handler(arguments)
    
    # User handlers (existing methods remain the same)
    async def _handle_create_user(self, arguments: dict) -> List[TextContent]:
        try:
            user = await self.user_service.create_user(arguments)
            response_text = f"✅ Usuário criado com sucesso!\n{json.dumps(user, indent=2, default=str)}"
            logger.info(f"✅ Usuário criado: {user['name']}")
            return [TextContent(type="text", text=response_text)]
        except ValueError as e:
            return [TextContent(type="text", text=f"❌ Erro: {str(e)}")]
    
    async def _handle_get_users(self, arguments: dict) -> List[TextContent]:
        users = await self.user_service.get_users()
        if not users:
            return [TextContent(type="text", text="📋 Nenhum usuário encontrado")]
        
        response_text = f"📋 Usuários encontrados ({len(users)}):\n{json.dumps(users, indent=2, default=str)}"
        logger.info(f"📋 Listando {len(users)} usuários")
        return [TextContent(type="text", text=response_text)]
    
    async def _handle_get_user_by_id(self, arguments: dict) -> List[TextContent]:
        user_id = arguments.get('user_id')
        
        if not ObjectId.is_valid(user_id):
            return [TextContent(type="text", text="❌ Erro: ID inválido")]
        
        user = await self.user_service.get_user_by_id(user_id)
        if not user:
            return [TextContent(type="text", text="❌ Erro: Usuário não encontrado")]
        
        response_text = f"👤 Usuário encontrado:\n{json.dumps(user, indent=2, default=str)}"
        return [TextContent(type="text", text=response_text)]
    
    async def _handle_update_user(self, arguments: dict) -> List[TextContent]:
        user_id = arguments.get('user_id')
        
        if not ObjectId.is_valid(user_id):
            return [TextContent(type="text", text="❌ Erro: ID inválido")]
        
        # Verificar se usuário existe
        user = await self.user_service.get_user_by_id(user_id)
        if not user:
            return [TextContent(type="text", text="❌ Erro: Usuário não encontrado")]
        
        # Validar email se fornecido
        if 'email' in arguments:
            if not UserValidator.is_valid_email(arguments['email']):
                return [TextContent(type="text", text="❌ Erro: Email inválido")]
            
            # Verificar se email já existe em outro usuário
            existing_user = self.db_manager.users_collection.find_one({
                'email': arguments['email'],
                '_id': {'$ne': ObjectId(user_id)}
            })
            if existing_user:
                return [TextContent(type="text", text="❌ Erro: Email já cadastrado para outro usuário")]
        
        # Atualizar dados
        update_data = {}
        allowed_fields = ['name', 'email', 'phone', 'age']
        
        for field in allowed_fields:
            if field in arguments:
                update_data[field] = arguments[field]
        
        if update_data:
            update_data['updated_at'] = datetime.utcnow()
            self.db_manager.users_collection.update_one(
                {'_id': ObjectId(user_id)},
                {'$set': update_data}
            )
        
        # Retornar usuário atualizado
        updated_user = self.db_manager.users_collection.find_one({'_id': ObjectId(user_id)})
        response_text = f"✅ Usuário atualizado com sucesso!\n{json.dumps(self.db_manager.serialize_user(updated_user), indent=2, default=str)}"
        return [TextContent(type="text", text=response_text)]
    
    async def _handle_delete_user(self, arguments: dict) -> List[TextContent]:
        user_id = arguments.get('user_id')
        
        if not ObjectId.is_valid(user_id):
            return [TextContent(type="text", text="❌ Erro: ID inválido")]
        
        result = await self.user_service.delete_user(user_id)
        
        if result:
            return [TextContent(type="text", text="✅ Usuário deletado com sucesso!")]
        else:
            return [TextContent(type="text", text="❌ Erro: Usuário não encontrado")]
    
    async def _handle_batch_create_users(self, arguments: dict) -> List[TextContent]:
        users_data = arguments.get('users', [])
        if not users_data:
            return [TextContent(type="text", text="❌ Erro: Lista de usuários é obrigatória")]
        
        created_users = []
        errors = []
        
        for i, user_info in enumerate(users_data):
            try:
                # Validações
                if not user_info.get('name'):
                    errors.append(f"Usuário {i+1}: Nome é obrigatório")
                    continue
                
                # Validar email apenas se fornecido
                email = user_info.get('email')
                if email and not UserValidator.is_valid_email(email):
                    errors.append(f"Usuário {i+1}: Email inválido")
                    continue
                
                # Verificar se email já existe
                if email and self.db_manager.users_collection.find_one({'email': email}):
                    errors.append(f"Usuário {i+1}: Email já cadastrado")
                    continue
                
                # Criar usuário
                user_data = {
                    'name': user_info['name'],
                    'email': email or '',
                    'phone': user_info.get('phone', ''),
                    'age': user_info.get('age'),
                    'created_at': datetime.utcnow(),
                    'updated_at': datetime.utcnow()
                }
                
                result = self.db_manager.users_collection.insert_one(user_data)
                user_data['_id'] = str(result.inserted_id)
                created_users.append(self.db_manager.serialize_user(user_data))
                
            except Exception as e:
                errors.append(f"Usuário {i+1}: {str(e)}")
        
        # Preparar resposta
        response_parts = []
        if created_users:
            response_parts.append(f"✅ {len(created_users)} usuário(s) criado(s) com sucesso:")
            response_parts.append(json.dumps(created_users, indent=2, default=str))
        
        if errors:
            response_parts.append(f"\n❌ {len(errors)} erro(s) encontrado(s):")
            response_parts.extend([f"- {error}" for error in errors])
        
        return [TextContent(type="text", text="\n".join(response_parts))]
    
    async def _handle_batch_operations(self, arguments: dict) -> List[TextContent]:
        operations = arguments.get('operations', [])
        if not operations:
            return [TextContent(type="text", text="❌ Erro: Lista de operações é obrigatória")]
        
        results = []
        errors = []
        
        for i, operation in enumerate(operations):
            try:
                action = operation.get('action')
                data = operation.get('data', {})
                
                if action == "create":
                    # Validações para criação
                    if not data.get('name'):
                        errors.append(f"Operação {i+1} (create): Nome é obrigatório")
                        continue
                    
                    email = data.get('email')
                    if email and not UserValidator.is_valid_email(email):
                        errors.append(f"Operação {i+1} (create): Email inválido")
                        continue
                    
                    if email and self.db_manager.users_collection.find_one({'email': email}):
                        errors.append(f"Operação {i+1} (create): Email já cadastrado")
                        continue
                    
                    # Criar usuário
                    user_data = {
                        'name': data['name'],
                        'email': email or '',
                        'phone': data.get('phone', ''),
                        'age': data.get('age'),
                        'created_at': datetime.utcnow(),
                        'updated_at': datetime.utcnow()
                    }
                    
                    result = self.db_manager.users_collection.insert_one(user_data)
                    user_data['_id'] = str(result.inserted_id)
                    results.append({
                        'operation': f'create_{i+1}',
                        'status': 'success',
                        'data': self.db_manager.serialize_user(user_data)
                    })
                
                elif action == "update":
                    user_id = data.get('user_id')
                    if not user_id or not ObjectId.is_valid(user_id):
                        errors.append(f"Operação {i+1} (update): ID inválido")
                        continue
                    
                    user = self.db_manager.users_collection.find_one({'_id': ObjectId(user_id)})
                    if not user:
                        errors.append(f"Operação {i+1} (update): Usuário não encontrado")
                        continue
                    
                    # Validar email se fornecido
                    if 'email' in data:
                        if data['email'] and not UserValidator.is_valid_email(data['email']):
                            errors.append(f"Operação {i+1} (update): Email inválido")
                            continue
                        
                        if data['email'] and self.db_manager.users_collection.find_one({
                            'email': data['email'],
                            '_id': {'$ne': ObjectId(user_id)}
                        }):
                            errors.append(f"Operação {i+1} (update): Email já cadastrado")
                            continue
                    
                    # Atualizar dados
                    update_data = {}
                    allowed_fields = ['name', 'email', 'phone', 'age']
                    
                    for field in allowed_fields:
                        if field in data:
                            update_data[field] = data[field]
                    
                    if update_data:
                        update_data['updated_at'] = datetime.utcnow()
                        self.db_manager.users_collection.update_one(
                            {'_id': ObjectId(user_id)},
                            {'$set': update_data}
                        )
                    
                    updated_user = self.db_manager.users_collection.find_one({'_id': ObjectId(user_id)})
                    results.append({
                        'operation': f'update_{i+1}',
                        'status': 'success',
                        'data': self.db_manager.serialize_user(updated_user)
                    })
                
                elif action == "delete":
                    user_id = data.get('user_id')
                    if not user_id or not ObjectId.is_valid(user_id):
                        errors.append(f"Operação {i+1} (delete): ID inválido")
                        continue
                    
                    result = self.db_manager.users_collection.delete_one({'_id': ObjectId(user_id)})
                    if result.deleted_count == 0:
                        errors.append(f"Operação {i+1} (delete): Usuário não encontrado")
                        continue
                    
                    results.append({
                        'operation': f'delete_{i+1}',
                        'status': 'success',
                        'message': f'Usuário {user_id} deletado'
                    })
                
                else:
                    errors.append(f"Operação {i+1}: Ação inválida '{action}'")
            
            except Exception as e:
                errors.append(f"Operação {i+1}: {str(e)}")
        
        # Preparar resposta
        response_parts = []
        if results:
            response_parts.append(f"✅ {len(results)} operação(ões) executada(s) com sucesso:")
            response_parts.append(json.dumps(results, indent=2, default=str))
        
        if errors:
            response_parts.append(f"\n❌ {len(errors)} erro(s) encontrado(s):")
            response_parts.extend([f"- {error}" for error in errors])
        
        return [TextContent(type="text", text="\n".join(response_parts))]
    
    async def _handle_export_users(self, arguments: dict) -> List[TextContent]:
        filename = arguments.get('filename', 'usuarios_export')
        filter_criteria = arguments.get('filter', {})
        selected_fields = arguments.get('fields', ['name', 'email', 'phone', 'age', 'created_at'])
        
        result_message = await self.export_service.export_users_csv(
            filename,
            filter_criteria,
            selected_fields
        )
        
        return [TextContent(type="text", text=result_message)]
    
    # New product handlers
    async def _handle_create_product(self, arguments: dict) -> List[TextContent]:
        try:
            product = await self.product_service.create_product(arguments)
            response_text = f"✅ Produto criado com sucesso!\n{json.dumps(product, indent=2, default=str)}"
            logger.info(f"✅ Produto criado: {product['name']}")
            return [TextContent(type="text", text=response_text)]
        except ValueError as e:
            return [TextContent(type="text", text=f"❌ Erro: {str(e)}")]
    
    async def _handle_get_products(self, arguments: dict) -> List[TextContent]:
        products = await self.product_service.get_products()
        if not products:
            return [TextContent(type="text", text="📦 Nenhum produto encontrado")]
        
        response_text = f"📦 Produtos encontrados ({len(products)}):\n{json.dumps(products, indent=2, default=str)}"
        logger.info(f"📦 Listando {len(products)} produtos")
        return [TextContent(type="text", text=response_text)]
    
    async def _handle_get_product_by_id(self, arguments: dict) -> List[TextContent]:
        product_id = arguments.get('product_id')
        
        if not ObjectId.is_valid(product_id):
            return [TextContent(type="text", text="❌ Erro: ID inválido")]
        
        product = await self.product_service.get_product_by_id(product_id)
        if not product:
            return [TextContent(type="text", text="❌ Erro: Produto não encontrado")]
        
        response_text = f"📦 Produto encontrado:\n{json.dumps(product, indent=2, default=str)}"
        return [TextContent(type="text", text=response_text)]
    
    async def _handle_update_product(self, arguments: dict) -> List[TextContent]:
        try:
            product_id = arguments.get('product_id')
            if not ObjectId.is_valid(product_id):
                return [TextContent(type="text", text="❌ Erro: ID inválido")]
            
            update_data = {k: v for k, v in arguments.items() if k != 'product_id'}
            product = await self.product_service.update_product(product_id, update_data)
            response_text = f"✅ Produto atualizado com sucesso!\n{json.dumps(product, indent=2, default=str)}"
            return [TextContent(type="text", text=response_text)]
        except ValueError as e:
            return [TextContent(type="text", text=f"❌ Erro: {str(e)}")]
    
    async def _handle_delete_product(self, arguments: dict) -> List[TextContent]:
        try:
            product_id = arguments.get('product_id')
            if not ObjectId.is_valid(product_id):
                return [TextContent(type="text", text="❌ Erro: ID inválido")]
            
            result = await self.product_service.delete_product(product_id)
            if result:
                return [TextContent(type="text", text="✅ Produto deletado com sucesso!")]
            else:
                return [TextContent(type="text", text="❌ Erro: Produto não encontrado")]
        except ValueError as e:
            return [TextContent(type="text", text=f"❌ Erro: {str(e)}")]
    
    async def _handle_search_products(self, arguments: dict) -> List[TextContent]:
        products = await self.product_service.search_products(arguments)
        if not products:
            return [TextContent(type="text", text="🔍 Nenhum produto encontrado com os filtros especificados")]
        
        response_text = f"🔍 Produtos encontrados ({len(products)}):\n{json.dumps(products, indent=2, default=str)}"
        return [TextContent(type="text", text=response_text)]
    
    # New purchase handlers
    async def _handle_create_purchase(self, arguments: dict) -> List[TextContent]:
        try:
            purchase = await self.purchase_service.create_purchase(arguments)
            response_text = f"🛒 Compra registrada com sucesso!\n{json.dumps(purchase, indent=2, default=str)}"
            logger.info(f"🛒 Compra registrada: {purchase['product_name']} x {purchase['quantity']}")
            return [TextContent(type="text", text=response_text)]
        except ValueError as e:
            return [TextContent(type="text", text=f"❌ Erro: {str(e)}")]
    
    async def _handle_get_user_purchases(self, arguments: dict) -> List[TextContent]:
        try:
            user_id = arguments.get('user_id')
            purchases = await self.purchase_service.get_user_purchases(user_id)
            if not purchases:
                return [TextContent(type="text", text="🛒 Nenhuma compra encontrada para este usuário")]
            
            response_text = f"🛒 Histórico de compras ({len(purchases)}):\n{json.dumps(purchases, indent=2, default=str)}"
            return [TextContent(type="text", text=response_text)]
        except ValueError as e:
            return [TextContent(type="text", text=f"❌ Erro: {str(e)}")]
    
    async def _handle_get_purchase_history(self, arguments: dict) -> List[TextContent]:
        limit = arguments.get('limit', 100)
        purchases = await self.purchase_service.get_purchase_history(limit)
        if not purchases:
            return [TextContent(type="text", text="🛒 Nenhuma compra encontrada")]
        
        response_text = f"🛒 Histórico geral de compras ({len(purchases)}):\n{json.dumps(purchases, indent=2, default=str)}"
        return [TextContent(type="text", text=response_text)]
    
    # New recommendation handlers
    async def _handle_get_user_recommendations(self, arguments: dict) -> List[TextContent]:
        try:
            user_id = arguments.get('user_id')
            limit = arguments.get('limit', 10)
            recommendations = await self.recommendation_service.get_recommendations_for_user(user_id, limit)
            
            if not recommendations:
                return [TextContent(type="text", text="💡 Nenhuma recomendação disponível")]
            
            response_text = f"💡 Recomendações personalizadas ({len(recommendations)}):\n{json.dumps(recommendations, indent=2, default=str)}"
            return [TextContent(type="text", text=response_text)]
        except ValueError as e:
            return [TextContent(type="text", text=f"❌ Erro: {str(e)}")]
    
    # New sample data handlers
    async def _handle_generate_sample_products(self, arguments: dict) -> List[TextContent]:
        try:
            products = await self.sample_data_generator.generate_sample_products()
            response_text = f"🏪 {len(products)} produtos de exemplo criados com sucesso!\n{json.dumps(products, indent=2, default=str)}"
            logger.info(f"🏪 Gerados {len(products)} produtos de exemplo")
            return [TextContent(type="text", text=response_text)]
        except Exception as e:
            return [TextContent(type="text", text=f"❌ Erro ao gerar produtos de exemplo: {str(e)}")]
    
    # New dashboard handler
    async def _handle_generate_dashboard(self, arguments: dict) -> List[TextContent]:
        try:
            dashboard_data = await self.dashboard_service.generate_dashboard()
            
            # Format dashboard output
            response_parts = [
                "📊 **DASHBOARD - VISÃO GERAL DO NEGÓCIO** 📊",
                "=" * 50,
                "",
                "📈 **RESUMO EXECUTIVO**",
                f"• Total de Usuários: {dashboard_data['overview']['total_users']}",
                f"• Total de Produtos: {dashboard_data['overview']['total_products']}",
                f"• Produtos em Estoque: {dashboard_data['overview']['products_in_stock']}",
                f"• Total de Vendas: {dashboard_data['overview']['total_purchases']}",
                f"• Receita Total: R$ {dashboard_data['overview']['total_revenue']}",
                f"• Ticket Médio: R$ {dashboard_data['overview']['average_order_value']}",
                "",
                "👥 **ANÁLISE DE USUÁRIOS**",
                f"• Taxa de Conversão: {dashboard_data['users']['conversion_rate']}%",
                f"• Compradores Ativos: {dashboard_data['users']['active_buyers']}",
                f"• Novos Usuários (30d): {dashboard_data['users']['recent_registrations_30d']}",
                "",
                "📦 **TOP PRODUTOS MAIS VENDIDOS**"
            ]
            
            # Add top products
            for i, product in enumerate(dashboard_data['products']['top_selling_products'][:3], 1):
                response_parts.append(f"{i}. {product['product_name']} - {product['total_quantity']} unidades (R$ {product['total_revenue']:.2f})")
            
            response_parts.extend([
                "",
                "⚠️ **ALERTAS DE ESTOQUE BAIXO**"
            ])
            
            # Add low stock alerts
            low_stock = dashboard_data['products']['low_stock_alerts'][:5]
            if low_stock:
                for product in low_stock:
                    response_parts.append(f"• {product['name']}: {product['stock_quantity']} unidades restantes")
            else:
                response_parts.append("• Nenhum produto com estoque baixo")
            
            response_parts.extend([
                "",
                "💰 **VENDAS POR CATEGORIA**"
            ])
            
            # Add sales by category
            for category in dashboard_data['sales']['sales_by_category'][:5]:
                response_parts.append(f"• {category['_id']}: R$ {category['total_revenue']:.2f} ({category['total_items_sold']} itens)")
            
            response_parts.extend([
                "",
                "🏆 **MELHORES CLIENTES**"
            ])
            
            # Add best customers
            for i, customer in enumerate(dashboard_data['sales']['best_customers'][:3], 1):
                response_parts.append(f"{i}. {customer['user_name']}: R$ {customer['total_spent']:.2f} ({customer['total_orders']} pedidos)")
            
            response_parts.extend([
                "",
                "🎯 **MÉTRICAS DE RECOMENDAÇÃO**",
                f"• Cobertura: {dashboard_data['recommendations']['recommendation_coverage']}%",
                f"• Usuários Elegíveis: {dashboard_data['recommendations']['users_eligible_for_recommendations']}",
                "",
                "🎨 **CORES MAIS POPULARES**"
            ])
            
            # Add popular colors
            for color in dashboard_data['recommendations']['color_preferences'][:5]:
                response_parts.append(f"• {color['_id']}: {color['preference_score']} vendas")
            
            response_parts.extend([
                "",
                f"📅 Gerado em: {dashboard_data['generated_at']}",
                "",
                "📋 **DADOS COMPLETOS EM JSON:**"
            ])
            
            formatted_response = "\n".join(response_parts)
            json_data = json.dumps(dashboard_data, indent=2, default=str)
            
            response_text = f"{formatted_response}\n\n```json\n{json_data}\n```"
            
            logger.info("📊 Dashboard gerado com sucesso")
            return [TextContent(type="text", text=response_text)]
            
        except Exception as e:
            logger.error(f"❌ Erro ao gerar dashboard: {e}")
            return [TextContent(type="text", text=f"❌ Erro ao gerar dashboard: {str(e)}")]
    
    async def run(self):
        logger.info("🎯 Iniciando servidor MCP...")
        try:
            async with stdio_server() as (read_stream, write_stream):
                logger.info("🔗 Streams criados, iniciando servidor...")
                await self.server.run(
                    read_stream, 
                    write_stream,
                    InitializationOptions(
                        server_name="store-management",
                        server_version="1.0.0",
                        capabilities={}
                    )
                )
        except KeyboardInterrupt:
            logger.info("🛑 Servidor interrompido pelo usuário")
        except Exception as e:
            logger.error(f"💥 Erro em main(): {e}")
            raise

# Initialize and run server
async def main():
    server = MCPUserServer()
    await server.run()

if __name__ == "__main__":
    print("=" * 50, file=sys.stderr, flush=True)
    print("🏪 MCP Store Management Server", file=sys.stderr, flush=True)
    print("=" * 50, file=sys.stderr, flush=True)
    
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error(f"💥 Erro fatal: {e}")
        sys.exit(1)