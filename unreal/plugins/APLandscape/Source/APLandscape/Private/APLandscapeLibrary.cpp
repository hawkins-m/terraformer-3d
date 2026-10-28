#include "APLandscapeLibrary.h"

#include "Editor.h"
#include "Landscape.h"
#include "LandscapeInfo.h"
#include "LandscapeProxy.h"
#include "Materials/MaterialInterface.h"
#include "Misc/FileHelper.h"
#include "Subsystems/UnrealEditorSubsystem.h"

DEFINE_LOG_CATEGORY_STATIC(LogAPLandscape, Log, All);

ALandscape* UAPLandscapeLibrary::CreateLandscapeFromRaw(const FString& RawPath, int32 SizeX, int32 SizeY, int32 SectionsPerComponent,
	int32 QuadsPerSection, FVector Location, FVector Scale, UMaterialInterface* Material, const FString& Label)
{
	const int32 QuadsPerComponent = SectionsPerComponent * QuadsPerSection;
	if (SizeX < 2 || SizeY < 2 || QuadsPerComponent < 1 || (SizeX - 1) % QuadsPerComponent || (SizeY - 1) % QuadsPerComponent)
	{
		UE_LOG(LogAPLandscape, Error, TEXT("%d x %d samples aren't whole components of %d quads"), SizeX, SizeY, QuadsPerComponent);
		return nullptr;
	}
	TArray<uint8> Bytes;
	if (!FFileHelper::LoadFileToArray(Bytes, *RawPath) || Bytes.Num() != SizeX * SizeY * 2)
	{
		UE_LOG(LogAPLandscape, Error, TEXT("%s: expected %d bytes of 16-bit heights"), *RawPath, SizeX * SizeY * 2);
		return nullptr;
	}
	TArray<uint16> Heights;
	Heights.SetNumUninitialized(SizeX * SizeY);
	FMemory::Memcpy(Heights.GetData(), Bytes.GetData(), Bytes.Num());

	UWorld* World = GEditor ? GEditor->GetEditorSubsystem<UUnrealEditorSubsystem>()->GetEditorWorld() : nullptr;
	if (!World)
	{
		UE_LOG(LogAPLandscape, Error, TEXT("no editor world"));
		return nullptr;
	}
	ALandscape* Landscape = World->SpawnActor<ALandscape>(Location, FRotator::ZeroRotator);
	Landscape->LandscapeMaterial = Material;
	Landscape->SetActorRelativeScale3D(Scale);

	TMap<FGuid, TArray<uint16>> HeightDataPerLayers;
	HeightDataPerLayers.Add(FGuid(), MoveTemp(Heights));
	TMap<FGuid, TArray<FLandscapeImportLayerInfo>> MaterialLayerDataPerLayers;
	MaterialLayerDataPerLayers.Add(FGuid(), TArray<FLandscapeImportLayerInfo>());
	Landscape->Import(FGuid::NewGuid(), 0, 0, SizeX - 1, SizeY - 1, SectionsPerComponent, QuadsPerSection, HeightDataPerLayers,
		*RawPath, MaterialLayerDataPerLayers, ELandscapeImportAlphamapType::Additive, TArrayView<const FLandscapeLayer>());
	if (ULandscapeInfo* Info = Landscape->GetLandscapeInfo())
	{
		Info->UpdateLayerInfoMap(Landscape);
	}
	Landscape->SetActorLabel(Label);
	UE_LOG(LogAPLandscape, Display, TEXT("%s: %d x %d samples, %d components"), *Label, SizeX, SizeY,
		((SizeX - 1) / QuadsPerComponent) * ((SizeY - 1) / QuadsPerComponent));
	return Landscape;
}
