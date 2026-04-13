"""
AI Service para FM Platform
- Auto-generación de OTs desde solicitudes
- Recomendaciones de mantenimiento predictivo
- Análisis automático de KPIs
- Sugerencias inteligentes basadas en histórico
"""

from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from enum import Enum
import random
from dataclasses import dataclass

class MaintenanceType(str, Enum):
    CORRECTIVE = "corrective"
    PREVENTIVE = "preventive"
    PREDICTIVE = "predictive"

@dataclass
class MaintenancePrediction:
    asset_id: str
    asset_name: str
    predicted_failure_date: str
    confidence_score: float  # 0-100
    recommended_action: str
    priority: str
    estimated_cost: float

@dataclass
class AIWorkOrderSuggestion:
    title: str
    description: str
    priority: str
    maintenance_type: MaintenanceType
    estimated_duration_hours: float
    recommended_technician_level: str  # junior, senior, specialist

class AIService:
    """Servicio de IA para automatización de FM"""
    
    def __init__(self):
        self.asset_failure_history = {}
        self.maintenance_patterns = {}
        self.technician_availability = {}
    
    async def analyze_request_and_generate_wo(
        self, 
        request_description: str, 
        asset_id: str,
        center_id: str,
        request_type: str = "general"
    ) -> AIWorkOrderSuggestion:
        """
        Analiza una solicitud y genera sugerencia de OT automáticamente
        Simula análisis NLP + histórico de mantenimiento
        """
        
        # Keywords en descripción para determinar tipo de mantenimiento
        keywords_preventive = ["mantenimiento", "rutina", "preventivo", "inspección"]
        keywords_urgent = ["falla", "no funciona", "urgente", "crítico", "emergencia"]
        keywords_predictive = ["degradación", "rendimiento bajo", "anomalía"]
        
        description_lower = request_description.lower()
        is_preventive = any(kw in description_lower for kw in keywords_preventive)
        is_urgent = any(kw in description_lower for kw in keywords_urgent)
        is_predictive = any(kw in description_lower for kw in keywords_predictive)
        
        # Determinar tipo de mantenimiento y prioridad
        if is_urgent or is_predictive:
            maintenance_type = MaintenanceType.CORRECTIVE if is_urgent else MaintenanceType.PREDICTIVE
            priority = "high" if is_urgent else "medium"
            estimated_hours = 2.0 if is_urgent else 3.5
        else:
            maintenance_type = MaintenanceType.PREVENTIVE if is_preventive else MaintenanceType.CORRECTIVE
            priority = "medium" if is_preventive else "low"
            estimated_hours = 1.5 if is_preventive else 2.0
        
        # Generar título inteligente
        title = self._generate_smart_title(request_description, maintenance_type)
        
        # Determinar nivel de técnico requerido
        tech_level = self._determine_technician_level(priority, maintenance_type)
        
        return AIWorkOrderSuggestion(
            title=title,
            description=request_description,
            priority=priority,
            maintenance_type=maintenance_type,
            estimated_duration_hours=estimated_hours,
            recommended_technician_level=tech_level
        )
    
    async def predict_asset_failures(
        self, 
        asset_id: str,
        asset_name: str,
        asset_category: str,
        failure_history: List[Dict[str, Any]],
        sensor_data: Optional[Dict[str, float]] = None
    ) -> MaintenancePrediction:
        """
        Predice fallas de activos basado en histórico + datos de sensores
        Simula análisis predictivo con datos históricos
        """
        
        if not failure_history:
            return MaintenancePrediction(
                asset_id=asset_id,
                asset_name=asset_name,
                predicted_failure_date=(datetime.now() + timedelta(days=90)).isoformat(),
                confidence_score=0.3,  # Baja confianza sin datos
                recommended_action="Comenzar recolección de datos para análisis predictivo",
                priority="low",
                estimated_cost=0
            )
        
        # Calcular MTBF (Mean Time Between Failures) desde histórico
        if len(failure_history) >= 2:
            days_between = []
            for i in range(len(failure_history) - 1):
                d1 = datetime.fromisoformat(failure_history[i]["date"])
                d2 = datetime.fromisoformat(failure_history[i+1]["date"])
                days_between.append(abs((d2 - d1).days))
            
            avg_mtbf = sum(days_between) / len(days_between)
            last_failure = datetime.fromisoformat(failure_history[-1]["date"])
            days_since_last = (datetime.now() - last_failure).days
            
            # Predicción: falla estimada en avg_mtbf desde la última
            predicted_failure = last_failure + timedelta(days=avg_mtbf)
            
            # Confianza basada en consistencia histórica
            if len(days_between) > 1:
                variance = sum((x - avg_mtbf) ** 2 for x in days_between) / len(days_between)
                std_dev = variance ** 0.5
                consistency = 1 - (std_dev / (avg_mtbf + 1))  # Normalizar
                confidence = max(0.5, min(0.95, consistency * 100))
            else:
                confidence = 0.6
            
            # Prioridad según proximidad a falla predicha
            days_until_failure = (predicted_failure - datetime.now()).days
            if days_until_failure < 7:
                priority = "high"
                action = f"⚠️ Mantenimiento URGENTE en {days_until_failure} días"
            elif days_until_failure < 30:
                priority = "medium"
                action = f"Programar mantenimiento predictivo en {days_until_failure} días"
            else:
                priority = "low"
                action = "Continuar monitoreo"
            
            # Estimación de costo (simulado)
            estimated_cost = self._estimate_maintenance_cost(asset_category, priority)
            
            return MaintenancePrediction(
                asset_id=asset_id,
                asset_name=asset_name,
                predicted_failure_date=predicted_failure.isoformat(),
                confidence_score=confidence,
                recommended_action=action,
                priority=priority,
                estimated_cost=estimated_cost
            )
        
        # Fallback si hay muy pocos datos
        return MaintenancePrediction(
            asset_id=asset_id,
            asset_name=asset_name,
            predicted_failure_date=(datetime.now() + timedelta(days=60)).isoformat(),
            confidence_score=0.4,
            recommended_action="Datos insuficientes; continuar monitoreo",
            priority="low",
            estimated_cost=0
        )
    
    async def analyze_kpi_trends(self, kpis: Dict[str, float]) -> Dict[str, Any]:
        """
        Analiza tendencias de KPIs y genera insights automáticos
        """
        insights = []
        alerts = []
        recommendations = []
        
        # Análisis MTTR
        if kpis.get("mttr_hours", 0) > 6:
            alerts.append("⚠️ MTTR alto (>6h): revisar asignación de técnicos")
            recommendations.append("Aumentar staff de mantenimiento o mejorar disponibilidad de repuestos")
        
        # Análisis SLA Compliance
        sla_compliance = kpis.get("sla_compliance_pct", 0)
        if sla_compliance < 95:
            alerts.append(f"⚠️ SLA Compliance bajo ({sla_compliance}%)")
            recommendations.append("Priorizar OTs cercanas a deadline")
        
        # Análisis de OTs abiertas
        open_wos = kpis.get("open_work_orders", 0)
        if open_wos > 10:
            alerts.append(f"⚠️ Muchas OTs abiertas ({open_wos})")
            recommendations.append("Revisar backlog y redistribuir carga de trabajo")
        
        # Análisis de costo por sqm
        cost_sqm = kpis.get("cost_per_sqm", 0)
        if cost_sqm > 15:
            insights.append("💡 Costo de mantenimiento por encima del promedio")
            recommendations.append("Considerar mantenimiento predictivo para reducir correctivos")
        
        # MTBF análisis
        mtbf_days = kpis.get("mtbf_days", 0)
        if mtbf_days < 21:
            alerts.append("⚠️ MTBF bajo (<21 días): activos poco confiables")
            recommendations.append("Realizar auditoría de activos críticos")
        
        return {
            "summary": f"Análisis de {len(kpis)} KPIs",
            "alerts": alerts,
            "insights": insights,
            "recommendations": recommendations,
            "overall_health": self._calculate_overall_health(kpis)
        }
    
    async def generate_automation_workflow(
        self,
        trigger_type: str,  # "sensor_threshold", "scheduled", "manual_request"
        asset_id: str,
        center_id: str,
        workflow_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Genera workflow de automatización para tareas repetitivas
        """
        
        workflow_steps = []
        
        if trigger_type == "sensor_threshold":
            workflow_steps = [
                {"step": 1, "action": "Crear solicitud de mantenimiento", "auto": True},
                {"step": 2, "action": "Generar OT automáticamente", "auto": True},
                {"step": 3, "action": "Asignar técnico disponible", "auto": True},
                {"step": 4, "action": "Notificar técnico", "auto": True},
                {"step": 5, "action": "Registrar en histórico", "auto": True},
            ]
        elif trigger_type == "scheduled":
            workflow_steps = [
                {"step": 1, "action": "Validar activos en ciclo de mantenimiento", "auto": True},
                {"step": 2, "action": "Generar OTs para cada activo", "auto": True},
                {"step": 3, "action": "Optimizar ruta de técnicos", "auto": False},
                {"step": 4, "action": "Asignar y notificar", "auto": True},
            ]
        else:  # manual_request
            workflow_steps = [
                {"step": 1, "action": "Analizar solicitud con IA", "auto": True},
                {"step": 2, "action": "Sugerir tipo de mantenimiento", "auto": False},
                {"step": 3, "action": "Crear OT", "auto": False},
                {"step": 4, "action": "Asignar recurso", "auto": False},
            ]
        
        return {
            "workflow_id": f"WF-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "trigger_type": trigger_type,
            "asset_id": asset_id,
            "center_id": center_id,
            "status": "created",
            "steps": workflow_steps,
            "created_at": datetime.now().isoformat(),
            "estimated_execution_time_minutes": len(workflow_steps) * 2
        }
    
    # ===== HELPER METHODS =====
    
    def _generate_smart_title(self, description: str, maintenance_type: MaintenanceType) -> str:
        """Genera título inteligente basado en descripción"""
        words = description.split()[:5]
        base_title = " ".join(words).capitalize()
        
        prefix_map = {
            MaintenanceType.CORRECTIVE: "[CORRECTIVO]",
            MaintenanceType.PREVENTIVE: "[PREVENTIVO]",
            MaintenanceType.PREDICTIVE: "[PREDICTIVO]",
        }
        
        return f"{prefix_map[maintenance_type]} {base_title}..."
    
    def _determine_technician_level(self, priority: str, maintenance_type: MaintenanceType) -> str:
        """Determina nivel de técnico requerido"""
        if priority == "high" or maintenance_type == MaintenanceType.PREDICTIVE:
            return "specialist"
        elif priority == "medium" or maintenance_type == MaintenanceType.CORRECTIVE:
            return "senior"
        else:
            return "junior"
    
    def _estimate_maintenance_cost(self, asset_category: str, priority: str) -> float:
        """Estima costo de mantenimiento"""
        base_costs = {
            "HVAC": 500,
            "ELECTRICAL": 350,
            "PLUMBING": 300,
            "STRUCTURAL": 800,
            "ELEVATOR": 1200,
            "SECURITY": 400,
            "LIGHTING": 200,
        }
        
        base = base_costs.get(asset_category.upper(), 400)
        multipliers = {"high": 1.5, "medium": 1.0, "low": 0.7}
        
        return base * multipliers.get(priority, 1.0)
    
    def _calculate_overall_health(self, kpis: Dict[str, float]) -> str:
        """Calcula salud general de la infraestructura"""
        sla = kpis.get("sla_compliance_pct", 0)
        mttr = kpis.get("mttr_hours", 0)
        mtbf = kpis.get("mtbf_days", 0)
        
        # Scoring simple
        score = 0
        if sla >= 95:
            score += 40
        elif sla >= 85:
            score += 30
        else:
            score += 10
        
        if mttr <= 4:
            score += 30
        elif mttr <= 6:
            score += 20
        else:
            score += 5
        
        if mtbf >= 28:
            score += 30
        elif mtbf >= 21:
            score += 20
        else:
            score += 5
        
        if score >= 85:
            return "🟢 Excelente"
        elif score >= 70:
            return "🟡 Bueno"
        elif score >= 50:
            return "🟠 Aceptable"
        else:
            return "🔴 Crítico"
