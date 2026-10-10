"""Create only synthetic data in the isolated transfer QA database."""
import os, json
from pathlib import Path
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'transfer_qa_settings')
import django
django.setup()
from django.conf import settings
assert str(settings.DATABASES['default']['NAME']).startswith('/private/tmp/ecommerce-transfer-qa')
from django.contrib.auth import get_user_model
from django.test import Client
from EcommerceApp.models import SiteSettings, Category, Brand, Product, ProductImage, HomeFeaturedProduct, HomeNovoProduct, HomeBestsellerProduct, Akcija, AkcijaQtyTier
from PIL import Image, ImageDraw
root=Path(settings.MEDIA_ROOT); (root/'qa').mkdir(parents=True,exist_ok=True)
for name, size, bg, label in [('logo',(480,76),'white','QA Webshop'), ('brand',(200,48),'white','QA BRAND'), ('product',(800,800),'#f5f5f5','QA PRODUCT'), ('detail',(800,800),'#ededed','QA DETAIL')]:
 im=Image.new('RGB',size,bg);dr=ImageDraw.Draw(im);dr.text((size[0]//3,size[1]//2),label,fill='#111111');im.save(root/'qa'/f'{name}.png')
site=SiteSettings.load(); site.logo='qa/logo.png';site.seo_organizacija_naziv='QA Webshop';site.seo_email='qa@example.invalid';site.kontakt_telefon='';site.company_name='QA Webshop';site.welcome_reg_popup_aktivan=False;site.product_dwell_popup_aktivan=False;site.browse_interest_popup_aktivan=False;site.save()
cat,_=Category.objects.get_or_create(slug='qa-kategorija',defaults={'naziv':'QA Kategorija'})
brand,_=Brand.objects.get_or_create(slug='qa-brand',defaults={'naziv':'QA Brand','slika':'qa/brand.png'})
products=[]
for i in range(1,9):
 p,_=Product.objects.get_or_create(slug=f'qa-artikal-{i}',defaults={'naziv':f'QA Artikal {i}','sifra':f'QA-{i:03}','cijena':'49.90','akcijska_cijena':'39.90' if i==1 else None,'stanje':100,'na_stanju':True,'pracenje_zaliha':False,'kategorija':cat,'brend':brand,'slika':'qa/product.png','opis':'Opis QA artikla.\nMaterijal: kvalitetan\nDužina: 200 cm'})
 products.append(p)
 for cls in [HomeFeaturedProduct,HomeNovoProduct,HomeBestsellerProduct]: cls.objects.get_or_create(artikal=p,defaults={'postavke':site,'redoslijed':i})
ProductImage.objects.get_or_create(product=products[0],slika='qa/detail.png')
deal,_=Akcija.objects.get_or_create(naziv='QA Količinski popust',defaults={'tip':Akcija.Tip.QTY_DEAL,'artikal':products[1]})
AkcijaQtyTier.objects.get_or_create(akcija=deal,quantity=3,defaults={'popust_postotak':10})
User=get_user_model();owner,_=User.objects.get_or_create(username='qa-owner',defaults={'is_staff':True,'is_superuser':True,'email':'owner@example.invalid'})
c=Client();c.force_login(owner);s=c.session;s['cart']={f'{p.pk}:0':{'product_id':p.pk,'variation_id':None,'quantity':2,'cijena':str(p.prikazna_cijena),'bazna_cijena':str(p.bazna_cijena),'na_akciji':bool(p.akcijska_cijena),'naziv':p.naziv,'product_naziv':p.naziv,'sifra':p.sifra} for p in products[:2]};s.save()
Path('/private/tmp/ecommerce-transfer-qa-session.json').write_text(json.dumps({'session':s.session_key}))
print('Synthetic QA fixtures ready')
