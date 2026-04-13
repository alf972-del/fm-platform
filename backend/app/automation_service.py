"""
Automation Service para FM Platform
- Workflows automáticos para tareas repetitivas
- Triggers basados en sensores y tiempo
- Escalation automática de OTs
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from enum import Enum
import asyncio

class AutomationTrigger(str, Enum):
    SENSOR_THRESHOLD = "sensor_threshold"
    SCHEDULED_TIME = "scheduled_time"
    SLA_APPROACHING = "sla_approaching"
    WO_STATUS_CHANGE = "wo_status_change"
    MANUAL = "manual"

class AutomationAction(str, Enum):
    CREATE_WO = "create_work_order"
    ESCALATE_WO = "escalate_work_order"
    ASSIGN_TECHNICIAN = "assign_technician"
    SEND_NOTIFICATION = "send_notification"
    CREATE_REQUEST = "create_request"
    UPDATE_ASSET = "update_asset"

class AutomationService:
    """Servicio de automatización de flujos"""
    
    def __init__(self):
        self.active_automations = {}
        self.automation_history = []
        self.trigger_handlers = {
            AutomationTrigger.SENSOR_THRESHOLD: self._handle_sensor_trigger,
            AutomationTrigger.SCHEDULED_TIME: self._handle_scheduled_trigger,
            AutomationTrigger.SLA_APPROACHING: self._handle_sla_trigger,
            AutomationTrigger.WO_STATUS_CHANGE: self._handle_status_change_trigger,
        }
    
    async def create_automation(
        self,
        name: str,
        trigger_type: AutomationTrigger,
        trigger_config: Dict[str, Any],
        actions: List[Dict[str, Any]],
        center_id: str,
        is_active: bool = True
    ) -> Dict[str, Any]:
        """Crea una nueva automatización"""
        
        automation_id = f"AUTO-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        automation = {
            "id": automation_id,
            "name": name,
            "trigger_type": trigger_type,
            "trigger_config": trigger_config,
            "actions": actions,
            "center_id": center_id,
            "is_active": is_active,
            "created_at": datetime.now().isoformat(),
            "execution_count": 0,
            "last_execution": None
        }
        
        self.active_automations[automation_id] = automation
        
        return {
            "status": "created",
            "automation_id": automation_id,
            "trigger_type": trigger_type,
            "actions_count": len(actions)
        }
    
    async def execute_automation(
        self,
        automation_id: str,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Ejecuta una automatización y retorna resultado"""
        
        automation = self.active_automations.get(automation_id)
        if not automation:
            return {"status": "error", "message": "Automation not found"}
        
        if not automation["is_active"]:
            return {"status": "skipped", "message": "Automation is disabled"}
        
        results = []
        
        # Ejecutar cada acción de la automatización
        for action_config in automation["actions"]:
            action_type = action_config.get("type")
            
            if action_type == AutomationAction.CREATE_WO:
                result = await self._action_create_wo(action_config, context)
            elif action_type == AutomationAction.ESCALATE_WO:
                result = await self._action_escalate_wo(action_config, context)
            elif action_type == AutomationAction.ASSIGN_TECHNICIAN:
                result = await self._action_assign_technician(action_config, context)
            elif action_type == AutomationAction.SEND_NOTIFICATION:
                result = await self._action_send_notification(action_config, context)
            elif action_type == AutomationAction.CREATE_REQUEST:
                result = await self._action_create_request(action_config, context)
            else:
                result = {"status": "unknown_action"}
            
            results.append({
                "action": action_type,
                "result": result
            })
        
        # Actualizar estadísticas
        automation["execution_count"] += 1
        automation["last_execution"] = datetime.now().isoformat()
        
        self.automation_history.append({
            "automation_id": automation_id,
            "executed_at": datetime.now().isoformat(),
            "results": results
        })
        
        return {
            "automation_id": automation_id,
            "status": "executed",
            "action_results": results,
            "total_actions": len(automation["actions"]),
            "successful_actions": len([r for r in results if r["result"].get("status") == "success"])
        }
    
    async def trigger_automation_by_condition(
        self,
        trigger_type: AutomationTrigger,
        trigger_data: Dict[str, Any],
        center_id: str
    ) -> List[Dict[str, Any]]:
        """Ejecuta todas las automatizaciones que coincidan con un trigger"""
        
        matching_automations = [
            auto for auto in self.active_automations.values()
            if auto["trigger_type"] == trigger_type and auto["center_id"] == center_id
        ]
        
        execution_results = []
        
        for automation in matching_automations:
            # Validar si el trigger cumple las condiciones
            if await self._validate_trigger_condition(automation, trigger_data):
                result = await self.execute_automation(automation["id"], trigger_data)
                execution_results.append(result)
        
        return execution_results
    
    # ===== TRIGGER HANDLERS =====
    
    async def _handle_sensor_trigger(
        self,
        sensor_reading: Dict[str, float],
        asset_id: str,
        center_id: str
    ) -> List[Dict[str, Any]]:
        """Maneja triggers de sensores (temperatura, presión, etc.)"""
        
        return await self.trigger_automation_by_condition(
            AutomationTrigger.SENSOR_THRESHOLD,
            {
                "sensor_reading": sensor_reading,
                "asset_id": asset_id,
                "center_id": center_id
            },
            center_id
        )
    
    async def _handle_scheduled_trigger(
        self,
        schedule_time: str,
        center_id: str
    ) -> List[Dict[str, Any]]:
        """Maneja triggers programados (cada X horas/días)"""
        
        return await self.trigger_automation_by_condition(
            AutomationTrigger.SCHEDULED_TIME,
            {"scheduled_time": schedule_time, "center_id": center_id},
            center_id
        )
    
    async def _handle_sla_trigger(
        self,
        work_order_id: str,
        sla_deadline: str,
        center_id: str
    ) -> List[Dict[str, Any]]:
        """Maneja escalation cuando SLA está a punto de vencer"""
        
        return await self.trigger_automation_by_condition(
            AutomationTrigger.SLA_APPROACHING,
            {
                "work_order_id": work_order_id,
                "sla_deadline": sla_deadline,
                "center_id": center_id
            },
            center_id
        )
    
    async def _handle_status_change_trigger(
        self,
        work_order_id: str,
        old_status: str,
        new_status: str,
        center_id: str
    ) -> List[Dict[str, Any]]:
        """Maneja cambios de estado en OTs"""
        
        return await self.trigger_automation_by_condition(
            AutomationTrigger.WO_STATUS_CHANGE,
            {
                "work_order_id": work_order_id,
                "old_status": old_status,
                "new_status": new_status,
                "center_id": center_id
            },
            center_id
        )
    
    # ===== ACTION HANDLERS =====
    
    async def _action_create_wo(
        self,
        action_config: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Acción: Crear OT automáticamente"""
        
        return {
            "status": "success",
            "action": "create_work_order",
            "message": f"WO creada: WO-AUTO-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "work_order_id": f"wo-{context.get('asset_id')}-{datetime.now().timestamp()}"
        }
    
    async def _action_escalate_wo(
        self,
        action_config: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Acción: Escalar OT (subir prioridad)"""
        
        return {
            "status": "success",
            "action": "escalate_work_order",
            "message": "OT escalada a prioridad: HIGH",
            "work_order_id": context.get("work_order_id")
        }
    
    async def _action_assign_technician(
        self,
        action_config: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Acción: Asignar técnico disponible"""
        
        # Simular búsqueda de técnico disponible
        available_technicians = ["TECH-001", "TECH-003", "TECH-005"]
        assigned_tech = available_technicians[0]
        
        return {
            "status": "success",
            "action": "assign_technician",
            "message": f"Técnico {assigned_tech} asignado",
            "technician_id": assigned_tech
        }
    
    async def _action_send_notification(
        self,
        action_config: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Acción: Enviar notificación"""
        
        notification_type = action_config.get("notification_type", "email")
        recipient = action_config.get("recipient", "team@fmplatform.io")
        
        return {
            "status": "success",
            "action": "send_notification",
            "message": f"Notificación enviada por {notification_type}",
            "recipient": recipient
        }
    
    async def _action_create_request(
        self,
        action_config: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Acción: Crear solicitud de mantenimiento"""
        
        return {
            "status": "success",
            "action": "create_request",
            "message": "Solicitud creada automáticamente",
            "request_id": f"req-{datetime.now().timestamp()}"
        }
    
    # ===== HELPER METHODS =====
    
    async def _validate_trigger_condition(
        self,
        automation: Dict[str, Any],
        trigger_data: Dict[str, Any]
    ) -> bool:
        """Valida si los datos del trigger cumplen las condiciones de la automatización"""
        
        config = automation["trigger_config"]
        
        # Validación simple: si hay sensor_reading, validar threshold
        if "sensor_reading" in trigger_data and "threshold" in config:
            sensor_value = trigger_data["sensor_reading"].get("value", 0)
            threshold = config["threshold"]
            comparison = config.get("comparison", ">")
            
            if comparison == ">" and sensor_value > threshold:
                return True
            elif comparison == "<" and sensor_value < threshold:
                return True
            elif comparison == "==" and sensor_value == threshold:
                return True
        
        # Si no hay condiciones especiales, permitir ejecución
        return True
    
    def get_automation_history(self, automation_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Retorna historial de ejecuciones de una automatización"""
        
        return [
            h for h in self.automation_history
            if h["automation_id"] == automation_id
        ][-limit:]
    
    def list_automations(self, center_id: str, is_active_only: bool = True) -> List[Dict[str, Any]]:
        """Lista automizaciones de un centro"""
        
        return [
            auto for auto in self.active_automations.values()
            if auto["center_id"] == center_id and (not is_active_only or auto["is_active"])
        ]
