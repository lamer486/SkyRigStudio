-- SkyRig Studio installer (run by mzp.run after the files are copied)
global SRS_inst_guard
global SRS_inst_choice
-- Max запускает установщик по-разному (fileIn: run, перетаскивание: drop); в mzp.run указаны оба, повтор отсекается
if SRS_inst_guard == undefined or (timeStamp() - SRS_inst_guard) > 2000 do
(
	local ver = (maxVersion())[1]
	local hasCorona = (for c in textureMap.classes where (c as string) == "CoronaBitmap" collect c).count > 0
	local warn1 = ""
	local warn2 = ""
	if ver < 27000 do warn1 = "! 3ds Max 2025 or newer is required (PySide6)."
	if not hasCorona do warn2 = "! Corona Renderer not found: the script works with Corona only."
	-- старая установка (SafeEnvironment) заменяется новой; данные пользователя не трогаются (скрипт сам переносит их из %APPDATA%\\SafeEnvironment)
	local legacy = ""
	local oldDir = (getDir #userScripts) + "\\SafeEnvironment"
	local oldMcr = (getDir #userMacros) + "\\SafeEnvironment-SafeEnvironment.mcr"
	if (doesFileExist oldDir) or (doesFileExist oldMcr) do
	(
		try
		(
			python.Execute ("import shutil, os\nshutil.rmtree(r'" + oldDir + "', ignore_errors=True)\nif os.path.isfile(r'" + oldMcr + "'): os.remove(r'" + oldMcr + "')")
			legacy = "The old version (SafeEnvironment) was removed. Add the 'SkyRig Studio' button to a toolbar again."
		) catch ()
	)
	-- зарегистрировать кнопку сейчас, без перезапуска Max
	local mcr = (getDir #userMacros) + "\\SkyRigStudio-SkyRigStudio.mcr"
	if doesFileExist mcr do fileIn mcr

	SRS_inst_choice = 0
	rollout SRS_inst_rol "SkyRig Studio"
	(
		label l1 "SkyRig Studio is installed." align:#left offset:[0,6]
		label l2 "Button: Customize > Customize User Interface >" align:#left offset:[0,6]
		label l3 "Toolbars (or Menus / Quads) > category 'SkyRig Studio'," align:#left
		label l4 "drag 'SkyRig Studio' onto a toolbar." align:#left
		label l5 warn1 align:#left offset:[0,6]
		label l6 warn2 align:#left
		label l7 legacy align:#left offset:[0,6]
		button b_run "Run now" width:130 height:26 across:3 offset:[0,10]
		button b_del "Uninstall" width:130 height:26 offset:[0,10]
		button b_close "Close" width:130 height:26 offset:[0,10]
		on b_run pressed do ( SRS_inst_choice = 1; destroyDialog SRS_inst_rol )
		on b_del pressed do ( SRS_inst_choice = 2; destroyDialog SRS_inst_rol )
		on b_close pressed do ( SRS_inst_choice = 0; destroyDialog SRS_inst_rol )
	)
	try ( destroyDialog SRS_inst_rol ) catch ()
	createDialog SRS_inst_rol 440 (if legacy != "" then 215 else 190) modal:true

	if SRS_inst_choice == 1 do
		try ( macros.run "SkyRig Studio" "SkyRigStudio" ) catch ( messageBox "Could not start. Restart 3ds Max and try again." title:"SkyRig Studio" )
	if SRS_inst_choice == 2 do
	(
		local un = (getDir #userScripts) + "\\SkyRigStudio\\uninstall.ms"
		if doesFileExist un then
		(
			SRS_uninst_guard = undefined
			fileIn un
		)
		else messageBox "Uninstaller file not found." title:"SkyRig Studio"
	)
	SRS_inst_guard = timeStamp()
)
