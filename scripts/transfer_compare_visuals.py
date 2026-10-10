"""Compare source and destination PNGs and recorded layout dimensions."""
import json,sys
from pathlib import Path
from PIL import Image,ImageChops,ImageStat
folder=Path(sys.argv[1] if len(sys.argv)>1 else 'reports/transfer-visual')
rows=json.loads((folder/'browser-results.json').read_text()); results=[]
for source in (row for row in rows if row['label']=='reference'):
    destination=next(row for row in rows if row['width']==source['width'] and row['route']==source['route'] and row['label']=='destination')
    a=Image.open(folder/source['file']).convert('RGB');b=Image.open(folder/destination['file']).convert('RGB')
    diff=ImageChops.difference(a,b) if a.size==b.size else None
    exact=significant=mean=None
    if diff is not None:
        r,g,bl=diff.split();maximum=ImageChops.lighter(ImageChops.lighter(r,g),bl)
        exact=maximum.point(lambda v:255 if v else 0).histogram()[255]/(a.width*a.height)
        significant=maximum.point(lambda v:255 if v>10 else 0).histogram()[255]/(a.width*a.height)
        mean=sum(ImageStat.Stat(diff).mean)/3
    results.append({'width':source['width'],'route':source['route'],'source_status':source['status'],'destination_status':destination['status'],'source_final_path':source['finalPath'],'destination_final_path':destination['finalPath'],'sizes_equal':a.size==b.size,'layout_equal':source['layout']==destination['layout'],'different_pixel_fraction':exact,'pixel_fraction_over_10_levels':significant,'mean_channel_difference':mean,'source_errors':source['errors'],'destination_errors':destination['errors'],'failed_assets':destination['failed']})
(folder/'comparison.json').write_text(json.dumps(results,indent=2)+'\n')
print('Compared',len(results),'page/width pairs')
for r in results:
    if not r['layout_equal'] or r['pixel_fraction_over_10_levels'] is None or r['pixel_fraction_over_10_levels']>.001 or r['destination_errors'] or r['failed_assets']:
        print(r)
