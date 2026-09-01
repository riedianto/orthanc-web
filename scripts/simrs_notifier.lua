-- SIMRS Notification Script for Orthanc (Lua)
-- Intercepts OnStoredInstance event, notifies SIMRS API, and auto-clears completed worklist

function OnStoredInstance(instanceId, tags, metadata, origin)
   local patientId = tags['PatientID'] or ''
   local patientName = tags['PatientName'] or ''
   local studyInstanceUid = tags['StudyInstanceUID'] or ''
   local accessionNumber = tags['AccessionNumber'] or ''
   local modality = tags['Modality'] or ''
   local sopInstanceUid = tags['SOPInstanceUID'] or ''

   -- 1. Read target webhook URL and POST notification to SIMRS
   local webhookUrl = os.getenv("SIMRS_WEBHOOK_URL")
   if not webhookUrl or webhookUrl == "" then
      webhookUrl = "http://192.168.188.207:8090/api/radiology/notify-stored"
   end

   local payload = string.format([[{"instanceId":"%s","patientId":"%s","patientName":"%s","studyInstanceUid":"%s","accessionNumber":"%s","modality":"%s","sopInstanceUid":"%s"}]],
      instanceId, patientId, patientName, studyInstanceUid, accessionNumber, modality, sopInstanceUid)

   pcall(function()
      HttpPost(webhookUrl, payload)
   end)

   -- 1b. Forward the received instance to the SATUSEHAT DICOM Router.
   -- Citra tetap disimpan di PACS lokal, dan salinannya dikirim ke router
   -- yang akan meneruskannya ke SATUSEHAT (cek ServiceRequest -> NIDR -> ImagingStudy).
   local satusehatModality = os.getenv("SATUSEHAT_ROUTER_MODALITY") or "SATUSEHAT"
   pcall(function()
      SendToModality(instanceId, satusehatModality)
   end)

   -- 2. Auto-remove worklist item once DICOM image is received via C-STORE (SCAN_COMPLETED)
   if accessionNumber and accessionNumber ~= "" then
      local safeAcc = string.gsub(accessionNumber, "[^%w%_%-]", "_")
      local jsonFile = "/var/lib/orthanc/worklists/order_" .. safeAcc .. ".json"
      local wlFile = "/var/lib/orthanc/worklists/order_" .. safeAcc .. ".wl"
      pcall(function()
         os.remove(jsonFile)
         os.remove(wlFile)
      end)
   end
end
