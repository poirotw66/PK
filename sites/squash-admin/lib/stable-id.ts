// Match the canonical local UUIDv5 identities without a Node-only crypto dependency.
export function stableId(namespace:string,...parts:string[]):string {
    const prefix=Uint8Array.from('6ba7b8119dad11d180b400c04fd430c8'.match(/../g)!,hex=>parseInt(hex,16));
    const name=new TextEncoder().encode('['+[namespace,...parts].map(value=>JSON.stringify(value)).join(', ')+']');
    const input=new Uint8Array(prefix.length+name.length);input.set(prefix);input.set(name,prefix.length);
    const padded=new Uint8Array(Math.ceil((input.length+9)/64)*64);padded.set(input);padded[input.length]=0x80;
    const data=new DataView(padded.buffer);data.setUint32(padded.length-4,input.length*8);
    const h=[0x67452301,0xefcdab89,0x98badcfe,0x10325476,0xc3d2e1f0],words=new Uint32Array(80);
    const rotate=(value:number,bits:number)=>(value<<bits)|(value>>>(32-bits));
    for(let offset=0;offset<padded.length;offset+=64){
        for(let i=0;i<16;i++)words[i]=data.getUint32(offset+4*i);
        for(let i=16;i<80;i++)words[i]=rotate(words[i-3]^words[i-8]^words[i-14]^words[i-16],1);
        let [a,b,c,d,e]=h;
        for(let i=0;i<80;i++){
            const f=i<20?(b&c)|(~b&d):i<40?b^c^d:i<60?(b&c)|(b&d)|(c&d):b^c^d;
            const k=i<20?0x5a827999:i<40?0x6ed9eba1:i<60?0x8f1bbcdc:0xca62c1d6;
            const next=(rotate(a,5)+f+e+k+words[i])>>>0;e=d;d=c;c=rotate(b,30);b=a;a=next;
        }
        [a,b,c,d,e].forEach((value,i)=>{h[i]=(h[i]+value)>>>0;});
    }
    const bytes=new Uint8Array(20),digest=new DataView(bytes.buffer);h.forEach((value,i)=>digest.setUint32(i*4,value));
    bytes[6]=(bytes[6]&15)|80;bytes[8]=(bytes[8]&63)|128;
    const hex=Array.from(bytes.slice(0,16),byte=>byte.toString(16).padStart(2,'0')).join('');
    return [hex.slice(0,8),hex.slice(8,12),hex.slice(12,16),hex.slice(16,20),hex.slice(20)].join('-');
}
