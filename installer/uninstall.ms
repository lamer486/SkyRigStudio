-- SkyRig Studio uninstaller
global SRS_uninst_guard
if SRS_uninst_guard == undefined or (timeStamp() - SRS_uninst_guard) > 2000 do
(
	local ttl = "SkyRig Studio"
	if queryBox "Uninstall SkyRig Studio (script and button)?\n\nYour library, presets, previews and backups will be kept." title:ttl do
	(
		local dir = (getDir #userScripts) + "\\SkyRigStudio"
		local mcr = (getDir #userMacros) + "\\SkyRigStudio-SkyRigStudio.mcr"
		local oldDir = (getDir #userScripts) + "\\SafeEnvironment"
		local oldMcr = (getDir #userMacros) + "\\SafeEnvironment-SafeEnvironment.mcr"
		python.Execute ("import shutil, os\nfor d in (r'" + dir + "', r'" + oldDir + "'):\n\tshutil.rmtree(d, ignore_errors=True)\nfor f in (r'" + mcr + "', r'" + oldMcr + "'):\n\tif os.path.isfile(f): os.remove(f)")
		local msg = "Uninstalled. The button disappears after 3ds Max is restarted."
		if queryBox "Also delete the library, presets and previews?\n(%APPDATA%\\SkyRigStudio, %LOCALAPPDATA%\\SkyRigStudio and the old %APPDATA%\\SafeEnvironment)\n\nBackups in Documents\\SkyRig Studio\\Backups are kept." title:ttl do
		(
			python.Execute "import shutil, os\nfor b, n in (('APPDATA', 'SkyRigStudio'), ('LOCALAPPDATA', 'SkyRigStudio'), ('APPDATA', 'SafeEnvironment')):\n\tr = os.environ.get(b, '')\n\tif r: shutil.rmtree(os.path.join(r, n), ignore_errors=True)"
			msg += "\nUser data deleted. Backups were kept."
		)
		messageBox msg title:ttl
	)

	SRS_uninst_guard = timeStamp()
)
