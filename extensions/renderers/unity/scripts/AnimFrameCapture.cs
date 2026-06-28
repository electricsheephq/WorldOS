using UnityEngine;
using UnityEditor;
public static class AnimFrameCapture {
  static int frameIdx = 0;
  static float[] nTs = {0.04f,0.12f,0.21f,0.29f,0.37f,0.46f,0.54f,0.62f,0.71f,0.79f,0.87f,0.96f};
  static Animator hA, gA;
  [MenuItem("Tools/Start Anim Capture")]
  public static void Start() {
    frameIdx=0;
    var h=GameObject.Find("HeroFighter"); hA=h?.GetComponentInChildren<Animator>();
    var g=GameObject.Find("MonsterGoblin"); gA=g?.GetComponentInChildren<Animator>();
    EditorApplication.update+=OnTick;
    UnityEngine.Debug.Log("CAPTURE: started");
  }
  static void OnTick() {
    if(frameIdx>=nTs.Length){EditorApplication.update-=OnTick;UnityEngine.Debug.Log("CAPTURE: done");return;}
    float nt=nTs[frameIdx];
    if(hA!=null){hA.Play("Attack",-1,nt);hA.Update(0f);}
    if(gA!=null){gA.Play("Attack",-1,(nt+0.1f)%1f);gA.Update(0f);}
    ScreenCapture.CaptureScreenshot("/Volumes/LEXAR/WorldOS-Unity-spike/Captures/anim_seq_"+(frameIdx+1).ToString("D2")+".png");
    UnityEngine.Debug.Log("CAPTURE frame "+(frameIdx+1)+" nT="+nt);
    frameIdx++;
  }
}