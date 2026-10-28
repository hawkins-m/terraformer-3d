// Terraformer: a Landscape from a raw heightmap, callable from editor Python
// (unreal.APLandscapeLibrary.create_landscape_from_raw), the way Landscape mode's Import does it.
#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "APLandscapeLibrary.generated.h"

class ALandscape;
class UMaterialInterface;

UCLASS()
class APLANDSCAPE_API UAPLandscapeLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	/** A new Landscape in the editor world from a little-endian 16-bit raw file of SizeX x SizeY
	 *  samples (rows along +Y), its first sample at Location, Scale cm per sample (Z: 100 = 256 m
	 *  over the 16-bit range, as Landscape mode), with Material and the actor Label. SizeX - 1 and
	 *  SizeY - 1 must be whole components (SectionsPerComponent x QuadsPerSection quads). Null on
	 *  a bad file or size (logged). */
	UFUNCTION(BlueprintCallable, Category = "AP|Landscape")
	static ALandscape* CreateLandscapeFromRaw(const FString& RawPath, int32 SizeX, int32 SizeY, int32 SectionsPerComponent,
		int32 QuadsPerSection, FVector Location, FVector Scale, UMaterialInterface* Material, const FString& Label);
};
