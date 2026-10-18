(() => {
 'use strict';
 const config=window.supplierInvoiceActions;
 async function extract(id) {
   const response=await fetch(config.base+id+'/extract',{method:'POST',headers:{'X-CSRFToken':config.token},cache:'no-store'});
   const result=await response.json().catch(()=>({error:response.status===413?"The file exceeds the upload limit. Choose a smaller file.":"The request could not complete. Please try again."}));
   if(!response.ok)throw new Error(result.error||'Extraction could not complete. Open the invoice details to retry.');
   window.location.assign(result.status==='processed'?config.base+id+'/details':result.review_url);
 }
 document.querySelectorAll('.extract-invoice').forEach(button=>button.addEventListener('click',async()=>{
   const message=document.getElementById('extract-message');button.disabled=true;message.textContent='Extracting invoice. This may take a moment.';
   try{await extract(button.dataset.id);}catch(error){message.textContent=error.message;button.disabled=false;}
 }));
 const form=document.getElementById('scan-invoice-form');
 if(form)form.addEventListener('submit',async event=>{
   event.preventDefault();const message=document.getElementById('scan-message'),fields=document.getElementById('scan-fields'),link=document.getElementById('scan-details');
   const file=document.getElementById('scan-camera').files[0]||document.getElementById('scan-file').files[0];
   if(!file){message.textContent='Choose an invoice file or take a photo first.';return;}
   if(!file.size||file.size>10*1024*1024){message.textContent='Choose a non-empty file of 10 MB or less.';return;}
   const data=new FormData(form);data.set('file',file);fields.disabled=true;link.hidden=true;message.textContent='Uploading original invoice...';
   let uploaded=false;
   try{
     const response=await fetch(config.upload,{method:'POST',headers:{'X-CSRFToken':config.token},body:data,cache:'no-store'});
     const result=await response.json().catch(()=>({error:response.status===413?"The file exceeds the upload limit. Choose a smaller file.":"The request could not complete. Please try again."}));if(!response.ok)throw new Error(result.error||'Upload failed. Please try again.');
     uploaded=true;link.href=config.base+result.invoice_id+'/details';link.hidden=false;message.textContent='Invoice uploaded. Extracting details...';
     await extract(result.invoice_id);
   }catch(error){message.textContent=(uploaded?'Your original invoice is saved. ':'')+error.message;if(!uploaded)fields.disabled=false;}
 });
})();
