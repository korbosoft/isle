#ifndef MXRENDERSETTINGS_H
#define MXRENDERSETTINGS_H

#include "decomp.h"
#include "mxtypes.h"

#include <d3d.h>
#include <ddraw.h>
#include <windows.h>

// SIZE 0x28
struct MxRenderSettings {
public:
	static MxU32 CopyFrom(MxRenderSettings& p_dest, const MxRenderSettings& p_src);

	undefined4 m_unk0x00;             // 0x00
	HWND m_hwnd;                      // 0x04
	IDirectDraw* m_directDraw;        // 0x08
	IDirectDrawSurface* m_ddSurface1; // 0x0c
	IDirectDrawSurface* m_ddSurface2; // 0x10
	MxU32 m_flags;                    // 0x14
	undefined4 m_unk0x18;             // 0x18
	MxU32 m_flags2;                   // 0x1c
	IDirect3D* m_direct3d;            // 0x20
	IDirect3DDevice* m_d3dDevice;     // 0x24
};

#endif // MXRENDERSETTINGS_H
