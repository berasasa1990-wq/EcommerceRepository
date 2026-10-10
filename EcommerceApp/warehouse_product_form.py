from django import forms
from .models import Product
from .forms import FlexibleDecimalField


class WarehouseNewProductDetailsForm(forms.ModelForm):
    rezim_zaliha = forms.ChoiceField(label='Način praćenja zaliha (obavezno)', choices=(
        ('tracked', 'Praćenje zaliha'), ('untracked', 'Bez praćenja'),
    ), widget=forms.RadioSelect, help_text='Praćenje: početna količina 0, dodajte zalihu na lokaciju. Bez praćenja: odmah dostupan, do ručnog skidanja sa stanja.')
    akcijska_cijena = FlexibleDecimalField(required=False, max_digits=10, decimal_places=2, min_value=0,
                                         label='Akcijska cijena (KM)')
    tagovi_unos = forms.CharField(required=False, label='Tagovi',
        help_text='Odvojite tagove zarezom. Novi tagovi se automatski dodaju.')
    tip = forms.TypedChoiceField(coerce=int, initial=0, choices=(
        (0, 'Normalno'), (3, 'Forsiraj — prvi među rezultatima pretrage'),
    ), label='Tip artikla')

    class Meta:
        model = Product
        fields = ('barkod', 'opis', 'pakovanje_komada', 'akcijska_cijena',
                  'meta_title', 'meta_description', 'h1_naslov', 'seo_tekst_iznad', 'seo_tekst_ispod')
        widgets = {'opis': forms.Textarea(attrs={'rows': 5}),
                   'seo_tekst_iznad': forms.Textarea(attrs={'rows': 3}),
                   'seo_tekst_ispod': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.order_fields(['rezim_zaliha'])
        self.fields['pakovanje_komada'].widget.attrs['min'] = 1
        self.fields['tip'].help_text = 'Forsirani artikal ide prvi kad odgovara pretrazi, uključujući pretragu unutar kategorije.'

    def clean_pakovanje_komada(self):
        value = self.cleaned_data.get('pakovanje_komada')
        if value is not None and value < 1:
            raise forms.ValidationError('Pakovanje mora imati najmanje jedan komad.')
        return value

    def clean_akcijska_cijena(self):
        value = self.cleaned_data.get('akcijska_cijena')
        if value is not None and value < 0:
            raise forms.ValidationError('Akcijska cijena ne može biti negativna.')
        return value
