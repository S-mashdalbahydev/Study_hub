"""Generate two tiny synthetic, CC0 PDF fixtures using basic PDF drawing commands.
This writes test documents; all PDF parsing is delegated to Docling.
"""
from pathlib import Path


def pdf(path, pages):
    objects = [b'', b'']
    objects.append(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
    page_ids = []
    for lines in pages:
        page_id = len(objects)+1
        content_id = page_id+1
        page_ids.append(page_id)
        commands = ['BT /F1 16 Tf 50 770 Td']
        for index,line in enumerate(lines):
            if index: commands.append('0 -28 Td /F1 12 Tf')
            line = line.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
            commands.append(f'({line}) Tj')
        commands.append('ET')
        stream = '\n'.join(commands).encode('ascii')
        objects.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>'.encode())
        objects.append(f'<< /Length {len(stream)} >>\nstream\n'.encode()+stream+b'\nendstream')
    objects[0] = b'<< /Type /Catalog /Pages 2 0 R >>'
    objects[1] = ('<< /Type /Pages /Count '+str(len(page_ids))+' /Kids ['+' '.join(f'{i} 0 R' for i in page_ids)+'] >>').encode()
    result = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for index,obj in enumerate(objects,1):
        offsets.append(len(result));result.extend(f'{index} 0 obj\n'.encode()+obj+b'\nendobj\n')
    xref = len(result)
    result.extend(f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]:result.extend(f'{offset:010d} 00000 n \n'.encode())
    result.extend(f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    path.write_bytes(result)


if __name__ == '__main__':
    folder = Path(__file__).resolve().parents[1]/'examples'
    folder.mkdir(exist_ok=True)
    pdf(folder/'helios-workshop.pdf',[
        ['Helios Workshop - Synthetic Study Document',
         'This fictional manual describes the Helios calibration station.',
         'The station uses a cobalt reference tile.',
         'Before calibration, the operator waits exactly 17 minutes.',
         'These are invented facts for testing document-grounded answers.',
         'This document is dedicated to the public domain under CC0.'],
        ['Helios Calibration Procedure',
         'Place the cobalt tile on the station and wait 17 minutes.',
         'Record the result in the orange ledger.',
         'If the lamp flashes twice, repeat the measurement.']])
    pdf(folder/'river-garden.pdf',[
        ['River Garden - Synthetic Study Document',
         'This fictional handbook describes the River Garden irrigation system.',
         'The garden uses an amber moisture marker.',
         'Each raised bed receives exactly 9 liters of water on watering day.',
         'These are invented facts for testing document-grounded answers.',
         'This document is dedicated to the public domain under CC0.'],
        ['River Garden Routine',
         'Check the amber marker before watering.',
         'Record each 9-liter delivery in the green notebook.',
         'This handbook contains no calibration station procedures.']])
    print('Created examples/helios-workshop.pdf and examples/river-garden.pdf')
