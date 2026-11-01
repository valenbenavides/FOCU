"""Esquema de datos: categorías cerradas válidas para perfil y convocatoria,
y sus etiquetas legibles para mostrar en la interfaz."""

TIPOS_BENEFICIARIO = ["grupo_semillero", "grupo_investigacion", "estudiante_pregrado",
                      "estudiante_posgrado", "docente", "investigador", "emprendedor"]
AREAS = ["ciencias_naturales", "ingenieria_tecnologia", "salud", "agro",
         "ciencias_sociales", "humanidades_artes", "multidisciplinar"]
ALCANCES = ["regional", "nacional", "internacional"]
FASES = ["idea", "prototipo", "ejecucion", "consolidacion"]

CATEGORIAS = {
    "tipo_beneficiario": TIPOS_BENEFICIARIO,
    "area_conocimiento": AREAS,
    "alcance": ALCANCES,
    "fase_proyecto": FASES,
}

# Etiquetas legibles para selectboxes y tarjetas (la interfaz muestra estas,
# el código interno sigue usando las claves cortas de arriba).
ETIQUETAS_TIPO_BENEFICIARIO = {
    "grupo_semillero": "Grupo de semillero de investigación",
    "grupo_investigacion": "Grupo de investigación",
    "estudiante_pregrado": "Estudiante de pregrado",
    "estudiante_posgrado": "Estudiante de posgrado",
    "docente": "Docente",
    "investigador": "Investigador",
    "emprendedor": "Emprendedor",
}
ETIQUETAS_AREA = {
    "ciencias_naturales": "Ciencias naturales",
    "ingenieria_tecnologia": "Ingeniería y tecnología",
    "salud": "Salud",
    "agro": "Agro",
    "ciencias_sociales": "Ciencias sociales",
    "humanidades_artes": "Humanidades y artes",
    "multidisciplinar": "Multidisciplinar",
}
ETIQUETAS_ALCANCE = {"regional": "Regional", "nacional": "Nacional", "internacional": "Internacional"}
ETIQUETAS_FASE = {"idea": "Idea", "prototipo": "Prototipo", "ejecucion": "Ejecución",
                  "consolidacion": "Consolidación"}

# Todas las etiquetas juntas, por campo (la API las entrega al frontend).
ETIQUETAS = {
    "tipo_beneficiario": ETIQUETAS_TIPO_BENEFICIARIO,
    "area_conocimiento": ETIQUETAS_AREA,
    "alcance": ETIQUETAS_ALCANCE,
    "fase_proyecto": ETIQUETAS_FASE,
}
