#!/usr/bin/env python3
"""Render the local film from actual capture/evidence, with timed Korean narration."""
import hashlib
import json
import math
import subprocess
import wave
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / 'production'
OUT = ROOT / 'deliverables'
W, H, FPS, SR = 1920, 1080, 30, 44100
FONT = '/System/Library/Fonts/AppleSDGothicNeo.ttc'
MONO = '/System/Library/Fonts/Menlo.ttc'
C = {'bg':'#F3F6FA','ink':'#172B45','muted':'#52647A','accent':'#006D77','line':'#CDDBE7','white':'#FFFFFF','soft':'#E0EFF1','dark':'#172B45'}


def run(args):
    subprocess.run([str(x) for x in args], check=True)


def font(size, bold=False, mono=False):
    return ImageFont.truetype(MONO if mono else FONT, size, index=(6 if bold and not mono else 0))


def wrap(text, f, max_width):
    measure = ImageDraw.Draw(Image.new('RGB',(1,1)))
    result = []
    for paragraph in text.split('\n'):
        words = paragraph.split(' ')
        line = ''
        for word in words:
            candidate = f'{line} {word}'.strip()
            if measure.textlength(candidate, font=f) > max_width and line:
                result.append(line)
                line = word
            else:
                line = candidate
        result.append(line)
    return result


def text(draw, xy, value, size=42, color=None, bold=False, width=1620, spacing=1.35, mono=False):
    f=font(size,bold,mono)
    x,y=xy
    lines=wrap(value,f,width)
    for line in lines:
        draw.text((x,y), line, font=f, fill=color or C['ink'])
        y+=int(size*spacing)
    return y


def contain(image, box):
    im=image.copy().convert('RGB')
    im.thumbnail((box[2]-box[0],box[3]-box[1]),Image.Resampling.LANCZOS)
    return im, (box[0]+(box[2]-box[0]-im.width)//2,box[1]+(box[3]-box[1]-im.height)//2)


def base(scene, index, total):
    im=Image.new('RGB',(W,H),C['bg'])
    d=ImageDraw.Draw(im)
    text(d,(92,42),'GitHub Copilot',25,bold=True)
    text(d,(1260,45),scene['chapter'],25,C['muted'],width=570)
    d.line((92,99,1828,99),fill=C['line'],width=2)
    d.line((92,1050,1828,1050),fill=C['line'],width=2)
    text(d,(92,1058),'사전 제작 데모 · 합성 데이터 · 외부 발송 없음',16,C['muted'])
    text(d,(1730,1058),f'{index+1:02d} / {total:02d}',16,C['muted'])
    return im,d


def draw_scene(scene,index,total):
    im,d=base(scene,index,total)
    kind=scene['kind']
    if kind in ('title','closing'):
        text(d,(96,187),'BUSINESS REQUEST → WORKING CHANGE',24,C['accent'],bold=True)
        y=text(d,(92,286),scene['title'],88,bold=True,width=1690,spacing=1.3)
        text(d,(96,y+42),scene.get('subtitle',''),40,C['muted'])
        d.rounded_rectangle((96,768,755,830),radius=12,fill=C['soft'])
        text(d,(122,781),'기존 코드  ·  실제 구현  ·  검토 근거',29,C['accent'],bold=True)
    elif kind in ('video','log'):
        text(d,(92,128),scene['title'],48,bold=True,width=1720)
        text(d,(95,195),scene.get('subtitle',''),26,C['muted'])
        if kind == 'log':
            path=P/'excerpts'/f"{scene['source']}.txt"
            if not path.exists():
                raise FileNotFoundError(f'Actual excerpt missing: {path}')
            value=path.read_text().strip()
            lines=value.splitlines()
            if len(lines)>14:
                raise ValueError(f'Choose a readable excerpt of <=14 lines: {path}')
            d.rounded_rectangle((92,265,1828,878),radius=18,fill=C['white'],outline=C['line'],width=2)
            text(d,(124,285),'실제 기록 발췌',22,C['accent'],bold=True)
            size=29
            if scene['source'] in ('plan','review'):
                size=31
            f=font(size,mono=False)
            while max((d.textlength(line,font=f) for line in lines),default=0)>1630 and size>21:
                size-=1; f=font(size)
            if max((d.textlength(line,font=f) for line in lines),default=0)>1630:
                raise ValueError(f'Excerpt has lines too long: {path}')
            y=345
            for line in lines:
                d.text((125,y),line,font=f,fill=C['ink'])
                y+=38
            if y>882:
                raise ValueError(f'Excerpt overflows frame: {path}')
    else:
        title_size=66 if '\n' in scene['title'] else 60
        y=text(d,(92,156),scene['title'],title_size,bold=True,width=1680)
        y=max(y+42,338)
        items=scene.get('items',[])
        if kind in ('scope','flow','handoff','pilot'):
            for i,item in enumerate(items):
                d.ellipse((101,y+9,116,y+24),fill=C['accent'])
                yy=text(d,(143,y),item,42,width=1630)
                y=yy+24
        else:
            for item in items:
                yy=text(d,(97,y),item,43,width=1670)
                y=yy+30
        if kind=='credits':
            text(d,(96,824),'공식 제품 소개자료 아님 · 효과/개발시간 보장 없음',25,C['muted'])
    path=P/'frames'/f'{index:02d}.png'
    im.save(path)
    return path


def caption_image(value,name):
    im=Image.new('RGBA',(W,H),(0,0,0,0))
    d=ImageDraw.Draw(im)
    f=font(37)
    lines=wrap(value,f,1640)
    if len(lines)>2:
        raise ValueError(f'Caption longer than two lines: {value}')
    y=920+(2-len(lines))*22
    d.rounded_rectangle((74,904,1846,1028),radius=13,fill=(23,43,69,247))
    for line in lines:
        x=(W-d.textlength(line,font=f))/2
        d.text((x,y),line,font=f,fill='white')
        y+=47
    dest=P/'frames'/f'{name}.png'
    im.save(dest)
    return dest


def sentences(value):
    # Explicit Korean sentence boundaries are retained in narration and SRT.
    import re
    return [p.strip() for p in re.findall(r'.+?(?:[.!?](?=\s|$)|$)',value) if p.strip()]


def synth(value,rate):
    key=hashlib.sha256(f'Yuna:{rate}:{value}'.encode()).hexdigest()[:18]
    aiff=P/'audio'/f'{key}.aiff'
    wav=P/'audio'/f'{key}.wav'
    if not wav.exists():
        run(['say','-v','Yuna','-r',str(rate),'-o',aiff,value])
        run(['ffmpeg','-v','error','-y','-i',aiff,'-ar',str(SR),'-ac','1','-c:a','pcm_s16le',wav])
    with wave.open(str(wav),'rb') as w:
        assert w.getframerate()==SR and w.getnchannels()==1 and w.getsampwidth()==2
        frames=w.readframes(w.getnframes())
    return frames,len(frames)/(2*SR)


def stamp(seconds):
    ms=round(seconds*1000)
    return f'{ms//3600000:02d}:{ms//60000%60:02d}:{ms//1000%60:02d},{ms%1000:03d}'


def main():
    for directory in ('frames','audio','segments','excerpts','clips'):
        (P/directory).mkdir(parents=True,exist_ok=True)
    OUT.mkdir(exist_ok=True)
    facts=json.loads((P/'facts.json').read_text())
    if not facts.get('copilotImplementation') or not facts.get('copilotReview') or facts['tests']['failed']:
        raise ValueError('Verified Copilot implementation, review and passing tests required for this narration.')
    scenes=json.loads((P/'storyboard.json').read_text())
    pieces=[]
    rate=185
    for scene in scenes:
        parts=[]
        for sentence in sentences(scene['narration']):
            pcm,seconds=synth(sentence,rate)
            parts.append({'text':sentence,'pcm':pcm,'duration':seconds})
        pieces.append(parts)
    speech=[sum(s['duration']+.22 for s in parts) for parts in pieces]
    minimum=[max(7,v+.9) for v in speech]
    if sum(minimum)>300:
        raise ValueError(f'Narration needs {sum(minimum):.1f}s. Shorten text rather than silently truncate.')
    extra=300-sum(minimum)
    weights=sum(scene['weight'] for scene in scenes)
    frames=[math.floor((m+extra*scene['weight']/weights)*FPS) for m,scene in zip(minimum,scenes)]
    frames[-1]+=300*FPS-sum(frames)
    cursor=0.0; cues=[]; timeline=[]; segments=[]; narration_pcm=bytearray()
    for i,(scene,parts,nframes) in enumerate(zip(scenes,pieces,frames)):
        duration=nframes/FPS
        frame=draw_scene(scene,i,len(scenes))
        segment_pcm=b'\0'*(int(.3*SR)*2)
        local=.3; overlays=[]
        for j,part in enumerate(parts):
            start=local; end=local+part['duration']
            cues.append((cursor+start,cursor+end,part['text']))
            cap=caption_image(part['text'],f'caption-{i:02d}-{j:02d}')
            overlays.append((cap,start,min(duration,end+.18)))
            segment_pcm+=part['pcm']+b'\0'*(int(.22*SR)*2)
            local=end+.22
        needed=nframes*SR//FPS*2
        if len(segment_pcm)>needed:
            raise ValueError('Narration audio overflow')
        segment_pcm+=b'\0'*(needed-len(segment_pcm))
        narration_pcm.extend(segment_pcm)
        output=P/'segments'/f'{i:02d}.mp4'
        cmd=['ffmpeg','-v','error','-y','-loop','1','-framerate',str(FPS),'-i',frame]
        filters=[]
        base_label='0:v'; next_input=1
        if scene['kind']=='video':
            clip=P/'clips'/f"{scene['source']}.mp4"
            if not clip.exists():
                raise FileNotFoundError(clip)
            cmd+=['-i',clip]
            filters.append(f'[{next_input}:v]scale=1736:646:force_original_aspect_ratio=decrease,pad=1736:646:(ow-iw)/2:(oh-ih)/2:color=0xF3F6FA,setsar=1,tpad=stop_mode=clone:stop_duration=30[clip]')
            filters.append('[0:v][clip]overlay=92:254:eof_action=pass[b0]')
            base_label='b0'; next_input+=1
        for j,(cap,start,end) in enumerate(overlays):
            cmd+=['-loop','1','-framerate',str(FPS),'-i',cap]
            label=f'cap{j}'
            filters.append(f'[{base_label}][{next_input}:v]overlay=0:0:enable=\'between(t,{start:.4f},{end:.4f})\'[{label}]')
            base_label=label; next_input+=1
        filters.append(f'[{base_label}]format=yuv420p[outv]')
        cmd+=['-filter_complex_threads','1','-filter_complex',';'.join(filters),'-map','[outv]','-an',
              '-frames:v',str(nframes),'-r',str(FPS),'-c:v','libx264','-preset','veryfast','-crf','20','-threads','4',
              '-movflags','+faststart',output]
        run(cmd)
        segments.append(output)
        timeline.append({'index':i,'start':cursor,'end':cursor+duration,'kind':scene['kind'],'source':scene.get('source'),'title':scene['title']})
        cursor+=duration
        print(f'RENDERED {i+1}/{len(scenes)} {duration:.2f}s · {scene["title"].replace(chr(10)," ")}',flush=True)
    concat=P/'segments'/'concat.txt'
    concat.write_text(''.join(f"file '{s.as_posix()}'\n" for s in segments))
    # Encode AAC only once: per-scene AAC priming offsets the concatenated film.
    audio=P/'audio'/'narration-ko.wav'
    with wave.open(str(audio),'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(narration_pcm)
    dest=OUT/'ghcp-cxo-demo-ko.mp4'
    run(['ffmpeg','-v','error','-y','-f','concat','-safe','0','-i',concat,'-i',audio,
         '-map','0:v:0','-map','1:a:0','-c:v','copy',
         '-af','loudnorm=I=-16:TP=-1.5:LRA=7','-c:a','aac','-b:a','160k','-ar',str(SR),
         '-t','300','-movflags','+faststart',dest])
    srt='\n'.join(f'{i+1}\n{stamp(a)} --> {stamp(b)}\n{t}\n' for i,(a,b,t) in enumerate(cues))
    (OUT/'ghcp-cxo-demo-ko.srt').write_text(srt)
    (P/'captions-ko.srt').write_text(srt)
    (P/'narration-ko.txt').write_text('\n\n'.join(s['narration'] for s in scenes))
    (P/'timeline.json').write_text(json.dumps(timeline,ensure_ascii=False,indent=2))
    print(f'FILM READY {dest}; timeline={cursor:.3f}s; narration={sum(speech):.2f}s',flush=True)


if __name__=='__main__':
    main()
