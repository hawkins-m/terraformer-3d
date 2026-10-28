using UnrealBuildTool;

public class APLandscape : ModuleRules
{
	public APLandscape(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
		PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", "Engine", "Landscape" });
		PrivateDependencyModuleNames.AddRange(new string[] { "UnrealEd" });
	}
}
