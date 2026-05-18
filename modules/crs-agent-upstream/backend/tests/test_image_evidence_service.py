from app.agent.domain.image_evidence.service import ImageEvidenceService


def test_image_evidence_service_coerces_near_schema_json_output():
    service = ImageEvidenceService()
    output = """
    {
      "image_evidence_id": null,
      "scene": "零部件/ECU铭牌",
      "summary": "图片为苏州国方汽车电子有限公司（Guofang）生产的电控单元（ECU）实物。关键识别信息包括零件号M0001/ECUA-00-000056、编号22080203及序列号2204005131。",
      "vehicle": "云内动力柴油发动机（推断）",
      "diagnosis": null,
      "visible_text": [
        "国方电子",
        "苏州国方汽车电子有限公司",
        "22080203",
        "M0001/ECUA-00-000056",
        "2204005131",
        "JS1037",
        "1RN217-1"
      ],
      "suggested_queries": [
        "国方电子 M0001 ECUA-00-000056 维修",
        "云内动力 国方电子ECU 22080203 电路图"
      ],
      "confidence": 0.9,
      "needs_user_confirm": false,
      "raw": "识别到金属外壳ECU。"
    }
    """

    evidence = service._parse_text_output(output)

    assert evidence.image_evidence_id.startswith("img_")
    assert evidence.scene.value == "document_hint"
    assert evidence.vehicle.brand == "云内动力柴油发动机（推断）"
    assert "M0001/ECUA-00-000056" in evidence.visible_text
    assert "云内动力 国方电子ECU 22080203 电路图" in evidence.suggested_queries
    assert evidence.raw["raw_output"] == "识别到金属外壳ECU。"


def test_image_evidence_service_coerces_nested_vehicle_and_diagnosis_fields():
    service = ImageEvidenceService()
    output = """
    {
      "image_evidence_id": "e8f9a2c1-4b7d-4e3a-9c8d-7f6e5a4b3c2d",
      "scene": "发动机控制单元 (ECU) / 计量单元控制器特写（拆卸状态）",
      "summary": "图片显示一个由苏州国方汽车电子有限公司生产的发动机控制器。",
      "vehicle": {
        "brand": "云内动力 (Cloud Yunnan) (推测)",
        "series": "未知",
        "model": "未知",
        "engine": "云内燃机 (YN)",
        "emission_standard": "待确认 (根据ECU型号可能为国III/国IV)"
      },
      "diagnosis": {
        "fault_code": null,
        "description": null,
        "system": "发动机电子控制系统 (EQUIA-00-000056)",
        "status": "历史状态待查"
      },
      "visible_text": [
        "22080203",
        "国方电子",
        "JS1037",
        "M0001 / EQUIA-00-000056",
        "苏州国方汽车电子有限公司",
        "2204005131"
      ],
      "suggested_queries": [
        "苏州国方 ECU 22080203",
        "国方电子 M0001 发动机控制器"
      ],
      "confidence": 0.9,
      "needs_user_confirm": true,
      "raw": "图片清晰展示了控制器金属外壳及三个标签。"
    }
    """

    evidence = service._parse_text_output(output)

    assert evidence.image_evidence_id == "e8f9a2c1-4b7d-4e3a-9c8d-7f6e5a4b3c2d"
    assert evidence.scene.value == "document_hint"
    assert evidence.vehicle.brand == "云内动力 (Cloud Yunnan) (推测)"
    assert evidence.vehicle.engine == "云内燃机 (YN)"
    assert evidence.vehicle.emission == "待确认 (根据ECU型号可能为国III/国IV)"
    assert evidence.diagnosis.ecu_model == "发动机电子控制系统 (EQUIA-00-000056)"
    assert evidence.diagnosis.status == "历史状态待查"
    assert "国方电子 M0001 发动机控制器" in evidence.suggested_queries
