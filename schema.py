"""Categorías válidas para perfiles y convocatorias, con sus etiquetas para la interfaz."""

TIPOS_BENEFICIARIO = ["grupo_semillero", "grupo_investigacion", "estudiante_pregrado",
                      "estudiante_posgrado", "docente", "investigador", "emprendedor"]
AREAS = ["ciencias_naturales", "ingenieria_tecnologia", "salud", "agro",
         "ciencias_sociales", "humanidades_artes", "multidisciplinar"]
ALCANCES = ["regional", "nacional", "internacional"]
FASES = ["idea", "prototipo", "ejecucion", "consolidacion"]

TIPOS_PERFIL = ["grupo_investigacion", "investigador", "docente",
                "estudiante_posgrado", "estudiante_pregrado"]

TIPO_FIJO = None


def con_tipo_fijo(perfil: dict) -> dict:
    """Copia del perfil con el tipo de beneficiario fijo, si hay uno."""
    return {**perfil, "tipo_beneficiario": TIPO_FIJO} if TIPO_FIJO else dict(perfil)


CATEGORIAS = {
    "tipo_beneficiario": TIPOS_BENEFICIARIO,
    "area_conocimiento": AREAS,
    "alcance": ALCANCES,
    "fase_proyecto": FASES,
}

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

ETIQUETAS = {
    "tipo_beneficiario": ETIQUETAS_TIPO_BENEFICIARIO,
    "area_conocimiento": ETIQUETAS_AREA,
    "alcance": ETIQUETAS_ALCANCE,
    "fase_proyecto": ETIQUETAS_FASE,
}

FACULTADES = [
    "Facultad de Artes",
    "Facultad de Ciencias Agrarias",
    "Facultad de Ciencias Contables, Económicas y Administrativas",
    "Facultad de Ciencias de la Salud",
    "Facultad de Ciencias Humanas y Sociales",
    "Facultad de Ciencias Naturales, Exactas y de la Educación",
    "Facultad de Derecho, Ciencias Políticas y Sociales",
    "Facultad de Ingeniería Civil",
    "Facultad de Ingeniería Electrónica y Telecomunicaciones",
]