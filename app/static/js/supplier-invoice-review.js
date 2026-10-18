(() => {
  'use strict';
  let state = JSON.parse(document.getElementById('review-state').textContent);
  let dirty = false;
  const form = document.getElementById('review-form');
  const body = document.getElementById('review-lines');
  const rows = [];
  const money = value => Number(value).toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2});
  const amount = value => value === null || value === '' ? null : Number(value);
  const changed = () => {dirty = true; document.getElementById('draft-state').textContent = 'Unsaved changes';};
  const node = (tag, className, text) => {const result = document.createElement(tag); result.className = className; if (text !== undefined) result.textContent = text; return result;};
  function candidates(index, products, query=false) {
    const row = rows[index];
    const list = row.querySelector('.match-results'); list.replaceChildren();
    row.querySelector('.match-caption').textContent = query ? 'Search results (choose to select)' : 'Suggested matches (not selected)';
    if (!products.length) {list.append(node('p','small text-muted','No matches found. You can refine the search or propose a new product.')); return;}
    products.slice(0,5).forEach(product => {
      const button = node('button', 'match-candidate'); button.type = 'button';
      button.append(node('span','fw-semibold',product.code + ' - ' + product.name));
      button.append(node('small','', (product.reasons.join(', ') || 'Inventory product') + ' | Stock: ' + product.current_stock));
      button.append(node('span','text-success mt-1','Use this product'));
      button.addEventListener('click', () => {const item=state.draft.items[index]; item.match_type='existing'; item.matched_product_id=product.id; state.previews[index].selected_product=product; state.previews[index].current_stock=product.current_stock; changed(); preview();});
      list.append(button);
    });
  }
  function preview() {
    const grouped = new Map(); let estimate=0, complete=true;
    state.draft.items.forEach(item => {if(item.match_type==='existing') grouped.set(item.matched_product_id,(grouped.get(item.matched_product_id)||0)+(amount(item.quantity)||0));});
    state.draft.items.forEach((item,index) => {
      const row=rows[index], meta=state.previews[index];
      const qty=amount(item.quantity), cost=amount(item.unit_cost), selection=row.querySelector('.match-selection');
      selection.replaceChildren();
      let current=null;
      if(item.match_type==='existing' && meta.selected_product) {
        selection.append(node('strong','','Selected for review'),node('span','',meta.selected_product.code+' - '+meta.selected_product.name)); current=amount(meta.current_stock);
      } else if(item.match_type==='new') {selection.append(node('strong','','New product proposal'),node('span','','Not created. Details above will be saved as a proposal.')); current=0;}
      else selection.append(node('span','text-warning','No product selected'));
      row.querySelector('.stock-current').textContent=current===null?'Choose a product':current;
      row.querySelector('.stock-add').textContent=qty===null?'Missing quantity':qty;
      row.querySelector('.stock-after').textContent=current===null||qty===null?'Not available':current+(item.match_type==='existing'?grouped.get(item.matched_product_id):qty);
      if(qty===null||cost===null||!Number.isFinite(qty)||!Number.isFinite(cost)){complete=false; row.querySelector('.line-estimate').textContent='Estimated cost: missing quantity or unit cost';}
      else {estimate+=qty*cost;row.querySelector('.line-estimate').textContent='Estimated cost (quantity x unit cost): '+money(qty*cost);}
      const issues=[]; if(qty!==null&&qty<=0) issues.push('Quantity must be greater than zero.'); if(cost!==null&&cost<0) issues.push('Unit cost cannot be negative.');
      if(qty!==null&&cost!==null&&item.line_total!==null&&Math.abs(qty*cost-(Number(item.line_total)-(state.draft.line_totals_include_vat===true?(amount(item.vat)||0):0)))>.02) issues.push('Quantity x unit cost differs from the line total excluding VAT.');
      row.querySelector('.line-check').textContent=issues.join(' ');
    });
    document.getElementById('purchase-estimate').textContent=(state.currency||'Currency not specified')+' '+money(estimate);
    document.getElementById('estimate-note').textContent=complete?'Preview only. No stock has been added.':'Partial estimate: some quantities or costs are missing.';
  }
  function warnings() {
    const list=document.getElementById('review-warnings'); list.replaceChildren();
    (state.warnings.length?state.warnings:['No additional warnings. Check the original before any future confirmation.']).forEach(text=>list.append(node('li','mb-1',text)));
  }
  state.draft.items.forEach((item,index) => {
    const row=document.getElementById('review-line-template').content.firstElementChild.cloneNode(true); body.append(row); rows.push(row);
    row.querySelector('.line-number').textContent='Line '+(index+1);
    row.classList.toggle('line-needs-review',state.previews[index].uncertain);
    row.querySelector('.line-uncertainty').hidden=!state.previews[index].uncertain;
    row.querySelector('.line-source-warnings').textContent=state.previews[index].source_warnings.join(' ');
    let timer,controller;
    async function search() {
      controller?.abort(); controller=new AbortController();
      const params=new URLSearchParams({q:row.querySelector('.product-search').value,code:item.product_code||'',tyre_size:item.tyre_size||'',brand:item.brand||'',pattern_model:item.pattern_model||''});
      try {const response=await fetch(window.invoiceReviewUrls.products+'?'+params,{cache:'no-store',signal:controller.signal}); const result=await response.json(); if(!response.ok) throw new Error(result.error||'Search unavailable'); candidates(index,result.products,!!params.get('q'));}
      catch(error){if(error.name!=='AbortError') row.querySelector('.match-caption').textContent='Search unavailable. Please try again.';}
    }
    row.querySelectorAll('[data-field]').forEach(input=> {
      const key=input.dataset.field;input.id='line-'+index+'-'+key;input.previousElementSibling.htmlFor=input.id;input.value=item[key]??'';
      input.addEventListener('input',()=>{item[key]=input.value===''?null:input.value;changed();preview();if(['product_code','brand','tyre_size','pattern_model'].includes(key)){clearTimeout(timer);timer=setTimeout(search,350);}});
    });
    const searchInput=row.querySelector('.product-search'); searchInput.id='product-search-'+index;row.querySelector('.search-label').htmlFor=searchInput.id;
    searchInput.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(search,350);});
    row.querySelector('.clear-match').addEventListener('click',()=>{item.match_type='unresolved';item.matched_product_id=null;state.previews[index].selected_product=null;changed();preview();});
    row.querySelector('.propose-product').addEventListener('click',()=>{item.match_type='new';item.matched_product_id=null;state.previews[index].selected_product=null;changed();preview();});
    candidates(index,state.previews[index].suggestions);
  });
  ['supplier_name','invoice_number','invoice_date','subtotal','vat','invoice_total'].forEach(key=>document.getElementById(key).addEventListener('input',event=>{state.draft[key]=event.target.value||null;changed();}));
  document.getElementById('line_totals_include_vat').addEventListener('change',event=>{state.draft.line_totals_include_vat=event.target.value===''?null:event.target.value==='true';changed();preview();});
  document.getElementById('open-confirmation').addEventListener('click',event=>{if(dirty){event.preventDefault();document.getElementById('review-message').textContent='Save your edits before opening final confirmation.';}});
  form.addEventListener('submit',async event=> {
    event.preventDefault();
    const button=document.getElementById('save-review'),fields=document.getElementById('review-fields'),message=document.getElementById('review-message');
    button.disabled=true;fields.disabled=true;message.textContent='Saving draft...';message.className='mt-3';
    try {
      const response=await fetch(window.invoiceReviewUrls.save,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':form.elements.csrf_token.value},body:JSON.stringify(state.draft),cache:'no-store'});
      const result=await response.json();
      if(!response.ok) {if(response.status===409) document.getElementById('reload-draft').hidden=false;throw new Error(result.error||'Draft could not be saved. Your edits are still on this page.');}
      // Keep line objects stable: input listeners refer to these same objects after saving.
      result.draft.items.forEach((item,index)=>Object.assign(state.draft.items[index],item));
      const draft=state.draft; Object.assign(draft,{...result.draft,items:draft.items}); state={...result,draft};
      rows.forEach((row,index)=>row.querySelectorAll('[data-field]').forEach(input=>{input.value=state.draft.items[index][input.dataset.field]??'';}));
      dirty=false;document.getElementById('draft-state').textContent='Saved draft v'+state.draft.version;
      document.getElementById('reload-draft').hidden=true;message.textContent='Draft saved. No products were created and inventory has not changed.';warnings();preview();
    } catch(error) {message.textContent=error.message;message.classList.add('review-message-error');}
    finally {button.disabled=false;fields.disabled=false;}
  });
  document.getElementById('cancel-review').addEventListener('click',event=>{if(dirty&&!window.confirm('Discard unsaved review edits? Your last saved draft will remain.'))event.preventDefault();else dirty=false;});
  window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
  warnings();preview();
})();
