"""
route_engine.py - Motor simples de rotas reais usando OSRM.
Versão 1: sem cache, sem progresso.
"""

import requests
import polyline
from math import cos, radians, sin, asin, sqrt

OSRM_URL = "http://router.project-osrm.org/route/v1/driving/"

def haversine_km(lat1, lon1, lat2, lon2):
    """Distância em linha reta (fallback)"""
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * 6371 * asin(sqrt(a))

def get_osrm_route(lat1, lon1, lat2, lon2):
    """Obtém rota real via OSRM. Retorna (distancia_km, lista_de_coordenadas) ou None."""
    try:
        # OSRM usa longitude,latitude
        coords = f"{lon1},{lat1};{lon2},{lat2}"
        url = f"{OSRM_URL}{coords}?overview=full&geometries=polyline"
        
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('code') == 'Ok' and data.get('routes'):
                route = data['routes'][0]
                distancia_km = route['distance'] / 1000
                geometria = route['geometry']
                coordenadas = polyline.decode(geometria)
                return distancia_km, coordenadas
    except Exception as e:
        print(f"Erro ao obter rota OSRM: {e}")
    
    return None, None

def get_route_with_fallback(lat1, lon1, lat2, lon2):
    """Tenta rota real, fallback para linha reta se falhar."""
    dist_real, coords_real = get_osrm_route(lat1, lon1, lat2, lon2)
    if dist_real is not None:
        return dist_real, coords_real, True
    else:
        dist_haversine = haversine_km(lat1, lon1, lat2, lon2)
        coords = [(lat1, lon1), (lat2, lon2)]  # linha reta simplificada
        return dist_haversine, coords, False
    