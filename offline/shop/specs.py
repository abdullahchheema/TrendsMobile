"""Product specifications for the website: the groups and fields the online team fills in once per phone model (shared by every variant).

Stored on Product.specs as {"Group title": {"Field label": "value", ...}, ...} so the website can render it as it is. Phones must have the
REQUIRED fields before the page can go live; accessories need none. Anything else can be added as a custom line under "Other"."""
from django.core.exceptions import ValidationError

GROUPS=[
 ('Display',[('display_size','Screen size','e.g. 6.9 inch'),('display_type','Display type','e.g. LTPO Super Retina XDR OLED'),('resolution','Resolution','e.g. 2868 x 1320'),('refresh_rate','Refresh rate','e.g. 120 Hz'),('brightness','Peak brightness','e.g. 2000 nits'),('screen_protection','Screen protection','e.g. Ceramic Shield 2')]),
 ('Performance',[('chipset','Chipset','e.g. Apple A19 Pro'),('cpu','CPU','e.g. 6-core'),('gpu','GPU','e.g. 6-core'),('ram','RAM','e.g. 12 GB'),('os','Operating system','e.g. iOS 26'),('storage_type','Storage type','e.g. NVMe')]),
 ('Camera',[('main_camera','Main camera','e.g. 48 MP wide + 48 MP ultra-wide + 48 MP telephoto'),('front_camera','Front camera','e.g. 18 MP'),('video','Video recording','e.g. 4K at 120 fps'),('camera_features','Camera features','e.g. Night mode, Cinematic mode')]),
 ('Battery',[('battery','Battery','e.g. 4800 mAh'),('charging','Wired charging','e.g. 40 W'),('wireless_charging','Wireless charging','e.g. 25 W MagSafe')]),
 ('Connectivity',[('network','Network','e.g. 5G, 4G LTE'),('sim','SIM','e.g. Dual eSIM / Nano-SIM + eSIM'),('wifi','Wi-Fi','e.g. Wi-Fi 7'),('bluetooth','Bluetooth','e.g. 6.0'),('nfc','NFC','Yes / No'),('usb','USB','e.g. USB-C 3.0'),('gps','GPS / navigation','e.g. GPS, GLONASS, Galileo')]),
 ('Body',[('dimensions','Dimensions','e.g. 163.4 x 77.6 x 8.25 mm'),('weight','Weight','e.g. 233 g'),('build','Build','e.g. Titanium frame, glass back'),('water_resistance','Water / dust resistance','e.g. IP68'),('biometrics','Biometrics','e.g. Face ID / fingerprint'),('sensors','Sensors','e.g. Accelerometer, gyro, barometer')]),
 ('In the box',[('box_contents','Box contents','e.g. Phone, USB-C cable, documents'),('warranty','Warranty','e.g. 1 year shop warranty')]),
]
REQUIRED=('display_size','chipset','ram','main_camera','battery','os')
OTHER='Other'
MAX_LEN=300
MAX_CUSTOM=30

LABEL={key:label for _,fields in GROUPS for key,label,_ in fields}
GROUP_OF={key:title for title,fields in GROUPS for key,_,_ in fields}


def form_groups(specs):
    """The editing form: every group with its fields filled from the saved specs, plus the custom lines."""
    specs=specs or {}
    out=[]
    for title,fields in GROUPS:
        saved=specs.get(title,{}) if isinstance(specs.get(title),dict) else {}
        out.append(dict(title=title,fields=[dict(key=key,label=label,hint=hint,value=saved.get(label,''),required=key in REQUIRED) for key,label,hint in fields]))
    custom=specs.get(OTHER,{}) if isinstance(specs.get(OTHER),dict) else {}
    return out,[dict(label=k,value=v) for k,v in custom.items()]


def parse(post):
    """Build the nested specs from the submitted form. Empty values are dropped; custom lines come as other_label_N / other_value_N."""
    specs={}
    for title,fields in GROUPS:
        group={}
        for key,label,_ in fields:
            value=' '.join(str(post.get('spec_'+key,'')).split())
            if len(value)>MAX_LEN:raise ValidationError(f'{label} is too long (at most {MAX_LEN} characters).')
            if value:group[label]=value
        if group:specs[title]=group
    custom={};n=0
    for name in sorted(k for k in post if k.startswith('other_label_')):
        index=name[len('other_label_'):];label=' '.join(str(post.get(name,'')).split());value=' '.join(str(post.get('other_value_'+index,'')).split())
        if not label and not value:continue
        if not label or not value:raise ValidationError('Each extra specification needs both a name and a value.')
        if label in LABEL.values():raise ValidationError(f'“{label}” already has its own field above.')
        n+=1
        if n>MAX_CUSTOM:raise ValidationError(f'At most {MAX_CUSTOM} extra specification lines.')
        if len(label)>80 or len(value)>MAX_LEN:raise ValidationError('An extra specification is too long.')
        custom[label]=value
    if custom:specs[OTHER]=custom
    return specs


def missing_required(specs):
    """Labels of the required specifications that are still empty."""
    specs=specs or {}
    return [LABEL[key] for key in REQUIRED if not (specs.get(GROUP_OF[key],{}) or {}).get(LABEL[key])]
