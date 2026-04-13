# 🚀 GUÍA DE PRUEBA RÁPIDA - FM PLATFORM FASE 1

## ⏱️ 5 MINUTOS PARA VER TODO FUNCIONANDO

### **PASO 1: Asegúrate que Docker está corriendo**
```bash
# Terminal/PowerShell:
docker ps

# Debería mostrar:
# - postgres-fm (PostgreSQL)
# - redis-fm (Redis cache)
# - api-fm (FastAPI)

# Si no están corriendo:
cd C:\Users\fredd\Proyectos\fm-platform
docker-compose up -d
```

### **PASO 2: Verifica que API está saludable**
```bash
# En terminal:
curl http://localhost:8000/health

# Respuesta esperada:
# {"status":"ok","version":"1.0.0","timestamp":"2024-..."}
```

### **PASO 3: Abre el Dashboard**
```bash
# En navegador (copiar y pegar):
file:///C:/Users/fredd/Proyectos/fm-platform/ai-dashboard.html

# Deberías ver un dashboard púrpura con 5 tabs
```

---

## 🧪 PRUEBAS RÁPIDAS POR FEATURE

### **PRUEBA 1: Análisis IA de Solicitud (NLP)** ⏱️ 30 segundos

1. En el dashboard, asegúrate estés en tab **"📋 Análisis de Solicitudes"**
2. En el campo "Descripción", borra lo que hay y escribe:
   ```
   La bomba de agua tiene un ruido extraño y la presión bajó
   ```
3. Click en botón azul **"🤖 Analizar con IA"**
4. Espera respuesta (2-3 segundos)

**Esperado:**
- ✅ Título automático sugerido
- ✅ Tipo: "corrective" (porque detecta "falla")
- ✅ Prioridad: "high" (porque es ruido + presión baja = urgente)
- ✅ Duración: ~2-3 horas
- ✅ Técnico: "specialist" (porque prioridad high)
- ✅ Confianza: 92%

---

### **PRUEBA 2: Predicción de Fallas (ML)** ⏱️ 30 segundos

1. Click en tab **"🔮 Predicción de Fallas"**
2. Los campos están pre-llenados con el Chiller (Asset ID)
3. Click en **"🔮 Predecir Falla"**
4. Espera respuesta

**Esperado:**
- ✅ Fecha predicha de falla
- ✅ Confianza entre 40-95%
- ✅ Acción recomendada (ej: "Mantenimiento en 7 días")
- ✅ Costo estimado
- ✅ Prioridad automática

---

### **PRUEBA 3: Análisis de KPIs** ⏱️ 30 segundos

1. Click en tab **"💡 KPI Insights"**
2. Click en **"📊 Analizar KPIs"**
3. Espera respuesta

**Esperado:**
- ✅ Salud general (🟢 Excelente / 🟡 Bueno / etc)
- ✅ KPIs actuales listados
- ✅ Alertas si hay problemas (⚠️)
- ✅ Recomendaciones accionables (✅)

---

### **PRUEBA 4: Crear Automatización** ⏱️ 1 minuto

1. Click en tab **"⚙️ Automaciones"**
2. En "Nombre de Automatización", escribe:
   ```
   Enfriamiento automático de emergencia
   ```
3. En "Tipo de Trigger", selecciona: **"Sensor - Threshold"**
4. Click en **"✨ Crear Automatización"**
5. Espera respuesta

**Esperado:**
- ✅ Automation ID generado
- ✅ Estado: "created"
- ✅ Trigger type: "sensor_threshold"
- ✅ 2 acciones configuradas

6. Luego, sin recargar, click en **"📋 Listar Automizaciones"**

**Esperado:**
- ✅ Tu automatización aparece en la lista
- ✅ Muestra nombre, trigger type, acciones, etc

---

### **PRUEBA 5: Lectura de Sensores IoT** ⏱️ 30 segundos

1. Click en tab **"📊 Lecturas de Sensores"**
2. Cambia el valor del sensor a **75** (para simular temperatura alta)
3. Click en **"📤 Enviar Lectura"**
4. Espera respuesta

**Esperado:**
- ✅ Sensor procesado
- ✅ "automations_triggered": 1 o más
- ✅ Resultados de ejecución de automizaciones

---

## 🔗 PRUEBAS AVANZADAS (OPCIONAL)

### **Test 1: API REST con cURL**

```bash
# Terminal:

# 1. Analizar solicitud
curl -X POST http://localhost:8000/v1/ai/analyze-request \
  -H "Content-Type: application/json" \
  -d '{
    "asset_id": "a8777219-5d7a-477c-9ef1-2394ea44f8bd",
    "center_id": "660e8400-e29b-41d4-a716-446655440001",
    "description": "El ascensor no sube correctamente",
    "request_type": "general"
  }'

# 2. Predecir falla
curl "http://localhost:8000/v1/ai/predict-failures?asset_id=a8777219-5d7a-477c-9ef1-2394ea44f8bd&center_id=660e8400-e29b-41d4-a716-446655440001"

# 3. Obtener KPI insights
curl "http://localhost:8000/v1/ai/kpi-insights?center_id=660e8400-e29b-41d4-a716-446655440001"

# 4. Listar automizaciones
curl "http://localhost:8000/v1/automations?center_id=660e8400-e29b-41d4-a716-446655440001"
```

### **Test 2: Swagger UI Interactivo**

```bash
# En navegador:
http://localhost:8000/docs

# Aquí puedes:
# 1. Ver todos los endpoints
# 2. Expandir cada uno
# 3. Click en "Try it out"
# 4. Modificar parámetros
# 5. Click en "Execute"
# 6. Ver respuesta en tiempo real
```

---

## 📊 ESPERAR QUE SUCEDA

### **Cuando analiza solicitud con IA:**
```
"El chiller no mantiene temperatura" 
    ↓
IA detecta: problema crítico + urgente
    ↓
Sugiere automáticamente:
- Tipo: CORRECTIVO
- Prioridad: HIGH
- Duración: 2 horas
- Técnico: SPECIALIST
```

### **Cuando predice falla:**
```
Historial de fallos del activo
    ↓
Calcula MTBF (30 días promedio)
    ↓
Predice próxima falla: +30 días desde última
    ↓
Confianza: 87%
    ↓
Recomienda acción: "Mantenimiento en 25 días"
```

### **Cuando analiza KPIs:**
```
7 KPIs en tiempo real (MTTR, MTBF, SLA, etc)
    ↓
IA detecta: SLA bajo (85% vs 95% objetivo)
    ↓
Genera alerta: "⚠️ SLA Compliance bajo"
    ↓
Recomienda: "Priorizar OTs cercanas a deadline"
```

### **Cuando creas automatización:**
```
Trigger: Temperatura > 50°C (sensor threshold)
    ↓
Acciones programadas:
1. Crear OT automáticamente
2. Enviar notificación al equipo
    ↓
Status: ACTIVE
    ↓
Cuando sensor > 50°C → DISPARA TODO AUTOMÁTICAMENTE
```

---

## ⚠️ POSIBLES PROBLEMAS & SOLUCIONES

| Problema | Causa | Solución |
|----------|-------|----------|
| "Cannot connect to API" | Docker no está corriendo | `docker-compose up -d` |
| 500 error en dashboard | Base de datos vacía | Revisar `docker logs postgres-fm` |
| Lentitud en respuestas | API sobrecargada | Espera 5 seg y reintentar |
| "Asset not found" | ID incorrecto | Usa: `a8777219-5d7a-477c-9ef1-2394ea44f8bd` |
| Confianza IA muy baja | Pocos datos históricos | Normal en primeras pruebas |

---

## 🎯 RESUMEN DE LO QUE PROBASTE

| Feature | Endpoint | Resultado |
|---------|----------|-----------|
| Análisis IA | `POST /v1/ai/analyze-request` | ✅ Genera OT automáticamente |
| Predicción | `GET /v1/ai/predict-failures` | ✅ Predice falla en X días |
| KPIs | `GET /v1/ai/kpi-insights` | ✅ Detecta anomalías + alerta |
| Automaciones | `POST /v1/automations` | ✅ Crea workflows automáticos |
| Sensores | `POST /v1/sensor-reading` | ✅ Dispara automizaciones |

---

## 🚀 SIGUIENTE PASO

Después de las pruebas:

1. ✅ Si todo funciona → Continuar a FASE 2 (Mobile + WebSocket)
2. ⚠️ Si hay errores → Revisar logs:
   ```bash
   docker logs api-fm  # Logs de API
   docker logs postgres-fm  # Logs de BD
   ```
3. 💬 Si tienes ideas → Documentar para Fase 2

---

**Total tiempo de pruebas:** 5 minutos ⏱️

**Resultado esperado:** Verás cómo IA analiza, predice, recomienda y automatiza sin intervención manual.

¡Listo! 🎉
